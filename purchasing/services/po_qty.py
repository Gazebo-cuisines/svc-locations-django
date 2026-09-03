from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderDelivery,
    PurchaseOrderDeliveryLine,
    PurchaseOrderHistory,
    PurchaseOrderHistoryEvent,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from stock_ledger.models import (
    StockEntry,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
)
from stock_ledger.util.services import _entry_is_reversed

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
    elif not any(line.qty_received > 0 for line in lines) and not holds:
        po.status = PurchaseOrderStatus.ORDERED
    po.save(update_fields=['status', 'updated_at'])


def _posting_for_entry(entry_id: int) -> StockEntryPosting | None:
    return (
        StockEntryPosting.objects
        .filter(stock_entry_id=entry_id)
        .first()
    )


def purchase_qty_from_entry(entry: StockEntry) -> Decimal | None:
    posting = _posting_for_entry(entry.id)
    raw = (posting.meta or {}).get('purchase_qty') if posting else None
    if raw not in (None, ''):
        purchase_qty = Decimal(str(raw))
        if purchase_qty > 0:
            return purchase_qty.quantize(Q6)

    if entry.source_document_type != 'po' or entry.source_document_line is None:
        return None
    line = (
        PurchaseOrderLine.objects
        .filter(
            purchase_order_id=entry.source_document_id,
            line_no=entry.source_document_line,
        )
        .only('product_supplier_id', 'multiplier')
        .first()
    )
    if line is None:
        return None
    stock_qty = abs(entry.quantity)
    if line.product_supplier_id:
        return stock_qty.quantize(Q6)
    multiplier = line.multiplier or Decimal('0')
    if multiplier > 0:
        return (stock_qty / multiplier).quantize(Q6)
    return stock_qty.quantize(Q6)


def _po_line_qty_was_applied(
    entry: StockEntry,
    posting: StockEntryPosting | None,
) -> bool:
    """True when this receipt's purchase qty sits on the PO line qty_received."""
    if posting is None:
        # Immediate receive (no queue) — qty applied at receive time.
        return True
    if posting.status == StockEntryPostingStatus.POSTED:
        return True
    return False


def _previous_live_receipt_entry_id(
    *,
    po_id: int,
    line_no: int,
    exclude_id: int,
) -> int | None:
    for candidate in (
        StockEntry.objects
        .filter(
            source_document_type='po',
            source_document_id=po_id,
            source_document_line=line_no,
            entry_type=StockEntryType.RECEIPT,
        )
        .exclude(pk=exclude_id)
        .order_by('-id')
    ):
        if _entry_is_reversed(candidate):
            continue
        posting = _posting_for_entry(candidate.id)
        if posting is not None and posting.status == StockEntryPostingStatus.CANCELLED:
            continue
        return candidate.id
    return None


def _line_qty_snapshot(line: PurchaseOrderLine) -> dict:
    return {
        'line_id': line.id,
        'line_no': line.line_no,
        'qty_ordered': str(line.qty_ordered),
        'qty_received': str(line.qty_received),
        'qty_rejected': str(line.qty_rejected),
        'qty_balance': str(line.qty_balance),
        'line_closed': line.line_closed,
        'stock_in_done': line.stock_in_done,
    }


def _find_delivery_line(
    *,
    line: PurchaseOrderLine,
    entry: StockEntry,
    posting: StockEntryPosting | None,
) -> PurchaseOrderDeliveryLine | None:
    meta = (posting.meta or {}) if posting else {}
    delivery_id = meta.get('delivery_id')
    if delivery_id not in (None, ''):
        dline = (
            PurchaseOrderDeliveryLine.objects
            .filter(delivery_id=int(delivery_id), po_line_id=line.id)
            .first()
        )
        if dline is not None:
            return dline
    dline = (
        PurchaseOrderDeliveryLine.objects
        .filter(po_line_id=line.id, last_receipt_entry_id=entry.id)
        .first()
    )
    if dline is not None:
        return dline
    return (
        PurchaseOrderDeliveryLine.objects
        .filter(po_line_id=line.id, qty_received__gt=0)
        .order_by('-updated_at', '-id')
        .first()
    )


def _rollback_delivery_line(
    *,
    line: PurchaseOrderLine,
    entry: StockEntry,
    posting: StockEntryPosting | None,
    purchase_qty: Decimal,
    next_receipt_entry_id: int | None,
) -> dict | None:
    dline = _find_delivery_line(line=line, entry=entry, posting=posting)
    if dline is None:
        return None
    before = str(dline.qty_received)
    dline.qty_received = max(
        Decimal('0'),
        (dline.qty_received - purchase_qty).quantize(Q6),
    )
    if dline.last_receipt_entry_id == entry.id:
        dline.last_receipt_entry_id = next_receipt_entry_id
    dline.save(
        update_fields=[
            'qty_received',
            'last_receipt_entry_id',
            'updated_at',
        ],
    )
    return {
        'delivery_id': dline.delivery_id,
        'delivery_line_id': dline.id,
        'qty_received_before': before,
        'qty_received_after': str(dline.qty_received),
    }


@transaction.atomic
def unapply_po_receipt_from_entry(
    entry: StockEntry,
    *,
    reason: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
) -> dict | None:
    """Roll PO line qty back when a PO-linked receipt is removed."""
    if entry.entry_type != StockEntryType.RECEIPT:
        return None
    if entry.source_document_type != 'po' or entry.source_document_id is None:
        return None
    if entry.source_document_line is None:
        return None

    purchase_qty = purchase_qty_from_entry(entry)
    if purchase_qty is None:
        return None

    posting = _posting_for_entry(entry.id)
    if posting is not None and (posting.meta or {}).get('po_rollback'):
        return dict((posting.meta or {})['po_rollback'])

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
        return None

    before = _line_qty_snapshot(line)
    po_line_applied = _po_line_qty_was_applied(entry, posting)
    next_receipt_entry_id = _previous_live_receipt_entry_id(
        po_id=entry.source_document_id,
        line_no=entry.source_document_line,
        exclude_id=entry.id,
    )

    if po_line_applied:
        line.qty_received = max(
            Decimal('0'),
            (line.qty_received - purchase_qty).quantize(Q6),
        )
        line.qty_balance = (
            line.qty_ordered - line.qty_received - line.qty_rejected
        ).quantize(Q6)
        if line.qty_balance < 0:
            line.qty_balance = Decimal('0')
        line.line_closed = line.qty_balance == 0
        line.stock_in_done = line.qty_balance == 0
        if line.last_receipt_entry_id == entry.id:
            line.last_receipt_entry_id = next_receipt_entry_id
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

    delivery_rollback = _rollback_delivery_line(
        line=line,
        entry=entry,
        posting=posting,
        purchase_qty=purchase_qty,
        next_receipt_entry_id=next_receipt_entry_id,
    )

    po = (
        PurchaseOrder.objects
        .select_for_update()
        .get(pk=entry.source_document_id)
    )
    recompute_po_status(po)

    after = _line_qty_snapshot(line)
    result = {
        'po_id': po.id,
        'po_number': po.external_number or po.number,
        'line_id': line.id,
        'line_no': line.line_no,
        'entry_id': entry.id,
        'entry_code': f'E{entry.id}',
        'purchase_qty': str(purchase_qty.normalize()),
        'po_line_applied': po_line_applied,
        'before': before,
        'after': after,
        'delivery': delivery_rollback,
        'po_status': po.status,
    }

    if posting is not None:
        meta = dict(posting.meta or {})
        meta['po_rollback'] = {
            **result,
            'reason': reason[:500],
            'rolled_back_at': timezone.now().isoformat(),
        }
        posting.meta = meta
        posting.save(update_fields=['meta'])

    delivery = None
    if delivery_rollback is not None:
        delivery = PurchaseOrderDelivery.objects.filter(
            pk=delivery_rollback['delivery_id'],
        ).first()

    PurchaseOrderHistory.objects.create(
        purchase_order=po,
        delivery=delivery,
        event_type=PurchaseOrderHistoryEvent.NOTE,
        remarks=(
            f'Stock receipt E{entry.id} removed — rolled back {purchase_qty} '
            f'purchase qty. {reason[:400]}'
        ),
        payload={
            **after,
            'entry_id': entry.id,
            'entry_code': f'E{entry.id}',
            'purchase_qty': str(purchase_qty.normalize()),
            'po_status': po.status,
            'delivery': delivery_rollback,
            'before_json': before,
            'after_json': after,
            'actor': {
                'user_id': actor_user_id,
                'lan_username': lan_username,
            },
        },
        actor_user_id=actor_user_id,
    )
    return result


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
