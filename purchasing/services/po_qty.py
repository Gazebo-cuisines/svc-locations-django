from decimal import Decimal

from purchasing.models import PurchaseOrder, PurchaseOrderLine, PurchaseOrderStatus
from stock_ledger.models import (
    StockEntry,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
)

Q6 = Decimal('0.000001')


def queued_hold_by_line_no(po_id: int) -> dict[int, Decimal]:
    """line_no → purchase qty sitting in queued receipts (not yet posted)."""
    holds: dict[int, Decimal] = {}
    rows = (
        StockEntryPosting.objects
        .filter(
            status=StockEntryPostingStatus.QUEUED,
            stock_entry__entry_type=StockEntryType.RECEIPT,
            stock_entry__source_document_type='po',
            stock_entry__source_document_id=po_id,
        )
        .values_list('stock_entry__source_document_line', 'meta')
    )
    for line_no, meta in rows:
        if line_no is None:
            continue
        raw = (meta or {}).get('purchase_qty')
        if raw in (None, ''):
            continue
        holds[line_no] = holds.get(line_no, Decimal('0')) + Decimal(str(raw))
    return holds


def queued_hold_for_line(line: PurchaseOrderLine) -> Decimal:
    return queued_hold_by_line_no(line.purchase_order_id).get(
        line.line_no, Decimal('0'),
    )


def recompute_po_status(po: PurchaseOrder) -> None:
    lines = list(po.lines.all())
    if not lines:
        return
    holds = queued_hold_by_line_no(po.id)
    if all(line.qty_balance == 0 for line in lines):
        po.status = PurchaseOrderStatus.RECEIVED
    elif (
        any(line.qty_received > 0 for line in lines)
        or any(qty > 0 for qty in holds.values())
    ):
        po.status = PurchaseOrderStatus.PARTIAL
    po.save(update_fields=['status', 'updated_at'])


def apply_po_receipt_from_entry(entry: StockEntry) -> None:
    """Move queued purchase qty onto the PO line after stock post."""
    if entry.source_document_type != 'po' or entry.source_document_id is None:
        return
    if entry.source_document_line is None:
        return
    posting = (
        StockEntryPosting.objects
        .filter(stock_entry_id=entry.id)
        .only('meta')
        .first()
    )
    raw = (posting.meta or {}).get('purchase_qty') if posting else None
    if raw in (None, ''):
        return
    purchase_qty = Decimal(str(raw))
    if purchase_qty <= 0:
        return
    line = (
        PurchaseOrderLine.objects
        .select_for_update()
        .filter(
            purchase_order_id=entry.source_document_id,
            line_no=entry.source_document_line,
        )
        .first()
    )
    if line is None:
        return
    line.qty_received = (line.qty_received + purchase_qty).quantize(Q6)
    line.qty_balance = (
        line.qty_ordered - line.qty_received - line.qty_rejected
    ).quantize(Q6)
    if line.qty_balance < 0:
        line.qty_balance = Decimal('0')
    line.last_receipt_entry_id = entry.id
    if line.qty_balance == 0:
        line.line_closed = True
        line.stock_in_done = True
    line.save(
        update_fields=[
            'qty_received',
            'qty_balance',
            'line_closed',
            'stock_in_done',
            'last_receipt_entry_id',
            'updated_at',
        ],
    )
    recompute_po_status(line.purchase_order)
