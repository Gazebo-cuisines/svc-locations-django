"""Nightly Ordered + Partial PO reminder (HTML + CSV), same look as closing stock."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from html import escape

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from purchasing.models import PurchaseOrderLine, PurchaseOrderStatus
from purchasing.serialize import _qty_str
from purchasing.services.open_pos import _pack_unit_name
from stock_ledger.models import StockReportEmailRecipient
from stock_ledger.util.closing_stock_email import (
    active_recipients,
    format_report_date,
    unsubscribe_url,
)
from stock_ledger.util.ses_mail import SesMailError, send_email_with_attachment

OPEN_PO_STATUSES = (
    PurchaseOrderStatus.ORDERED,
    PurchaseOrderStatus.PARTIAL,
)

_STATUS_LABEL = {
    PurchaseOrderStatus.ORDERED: 'Not received yet',
    PurchaseOrderStatus.PARTIAL: 'Part received',
}

_CSV_FIELDS = (
    'po_id',
    'po_number',
    'status',
    'ordered_at',
    'expected_at',
    'supplier_name',
    'ship_to',
    'product_name',
    'recipe_code',
    'qty_ordered',
    'qty_received',
    'qty_balance',
    'unit_name',
    'shape_format_label',
)


def po_page_url(po_id: int) -> str:
    base = getattr(
        settings, 'PUBLIC_WEB_BASE_URL', 'https://beta.gazeboo.cloud',
    ).rstrip('/')
    return f'{base}/purchasing/pos/{po_id}'


def open_po_reminder_rows(as_of: date | None = None) -> list[dict]:
    day = as_of or timezone.localdate()
    lines = (
        PurchaseOrderLine.objects.filter(
            purchase_order__status__in=OPEN_PO_STATUSES,
            qty_balance__gt=0,
        )
        .filter(
            Q(purchase_order__expected_at__lte=day)
            | Q(purchase_order__expected_at__isnull=True),
        )
        .select_related(
            'purchase_order',
            'purchase_order__supplier',
            'purchase_order__ship_to_location',
            'product',
            'unit',
            'product_supplier__outer_unit',
        )
        .order_by(
            F('purchase_order__ordered_at').desc(nulls_last=True),
            '-purchase_order_id',
            'line_no',
        )
    )
    rows = []
    for line in lines:
        po = line.purchase_order
        rows.append({
            'po_id': po.id,
            'po_number': po.external_number or po.number or '',
            'status': po.status,
            'ordered_at': po.ordered_at.isoformat() if po.ordered_at else '',
            'expected_at': po.expected_at.isoformat() if po.expected_at else '',
            'supplier_name': po.supplier.name if po.supplier_id else '',
            'ship_to': (
                po.ship_to_location.name if po.ship_to_location_id else ''
            ),
            'product_name': line.product.name if line.product_id else '',
            'recipe_code': (
                line.product.recipe_code if line.product_id else ''
            ),
            'qty_ordered': _qty_str(line.qty_ordered) or '0',
            'qty_received': _qty_str(line.qty_received) or '0',
            'qty_balance': _qty_str(line.qty_balance) or '0',
            'unit_name': _pack_unit_name(line) or '',
            'shape_format_label': line.shape_format_label or '',
        })
    return rows


def consolidated_po_rows(line_rows: list[dict]) -> list[dict]:
    """One row per PO. Same product + unit on a PO is summed."""
    by_po: dict[int, dict] = {}
    order: list[int] = []
    for row in line_rows:
        pid = row['po_id']
        slot = by_po.get(pid)
        if slot is None:
            slot = {
                'po_id': pid,
                'po_number': row['po_number'],
                'status': row['status'],
                'status_label': _STATUS_LABEL.get(row['status'], row['status']),
                'ordered_at': row['ordered_at'],
                'expected_at': row['expected_at'],
                'supplier_name': row['supplier_name'],
                'ship_to': row['ship_to'],
                'items': {},
            }
            by_po[pid] = slot
            order.append(pid)
        key = (row['product_name'], row['unit_name'])
        slot['items'][key] = slot['items'].get(key, Decimal('0')) + Decimal(
            str(row['qty_balance'] or 0),
        )
    out = []
    for pid in order:
        slot = by_po[pid]
        parts = []
        for (name, unit), qty in slot['items'].items():
            head = ' '.join(x for x in (_qty_str(qty) or '0', unit) if x)
            parts.append(f'{name} {head}'.strip() if name else head)
        slot['still_to_come'] = '; '.join(parts)
        del slot['items']
        out.append(slot)
    return out


def rows_to_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, '') for k in _CSV_FIELDS})
    return buf.getvalue().encode('utf-8')


def _day_label(iso: str) -> str:
    if not iso:
        return '—'
    try:
        return format_report_date(date.fromisoformat(iso))
    except ValueError:
        return iso


def _list_html(pos: list[dict]) -> str:
    if not pos:
        return '<p style="margin:0 0 16px;">There are no open purchase orders.</p>'
    rows_html = []
    for po in pos:
        href = escape(po_page_url(po['po_id']), quote=True)
        no = escape(str(po['po_number'] or po['po_id']))
        rows_html.append(
            '<tr>'
            f'<td style="padding:4px 6px;border-bottom:1px solid #e6e8ec;line-height:1.25;">'
            f'<a href="{href}" style="color:#c45f12;font-weight:bold;">{no}</a>'
            f'</td>'
            f'<td style="padding:4px 6px;border-bottom:1px solid #e6e8ec;line-height:1.25;">'
            f'{escape(po["supplier_name"] or "—")}</td>'
            f'<td style="padding:4px 6px;border-bottom:1px solid #e6e8ec;line-height:1.25;">'
            f'{escape(po["status_label"])}</td>'
            f'<td style="padding:4px 6px;border-bottom:1px solid #e6e8ec;line-height:1.25;">'
            f'{escape(_day_label(po["expected_at"]))}</td>'
            f'<td style="padding:4px 6px;border-bottom:1px solid #e6e8ec;line-height:1.25;">'
            f'{escape(po["still_to_come"] or "—")}</td>'
            '</tr>'
        )
    body = ''.join(rows_html)
    return f'''
            <p style="margin:0 0 8px;"><strong>Please take action on the items below.</strong></p>
            <p style="margin:0 0 8px;font-size:13px;">
              These purchase orders are still open and the delivery date is today or already passed:
              nothing received yet, or only part received.
              Click a PO number to open it.
            </p>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:12px;color:#222;line-height:1.25;">
              <tr style="background:#f4f5f7;">
                <th align="left" style="padding:4px 6px;border-bottom:1px solid #c5c9d0;">PO</th>
                <th align="left" style="padding:4px 6px;border-bottom:1px solid #c5c9d0;">Supplier</th>
                <th align="left" style="padding:4px 6px;border-bottom:1px solid #c5c9d0;">Status</th>
                <th align="left" style="padding:4px 6px;border-bottom:1px solid #c5c9d0;">Delivery</th>
                <th align="left" style="padding:4px 6px;border-bottom:1px solid #c5c9d0;">Still to come</th>
              </tr>
              {body}
            </table>
            <p style="margin:8px 0 0;font-size:12px;color:#555;">A CSV with line detail is also attached.</p>'''


def _list_text(pos: list[dict]) -> str:
    if not pos:
        return 'There are no open purchase orders.\n'
    lines = ['Please take action on the items below.\n']
    for po in pos:
        lines.append(
            f"- PO {po['po_number'] or po['po_id']} · {po['supplier_name'] or '—'} · "
            f"{po['status_label']} · delivery {_day_label(po['expected_at'])} · "
            f"{po['still_to_come'] or '—'} · {po_page_url(po['po_id'])}"
        )
    return '\n'.join(lines) + '\n'


def report_file_date(value: date) -> str:
    """17.09.2026"""
    return value.strftime('%d.%m.%Y')


def build_open_po_html(
    *,
    as_of: date,
    pos: list[dict] | None = None,
    unsubscribe_href: str | None = None,
) -> str:
    file_date = report_file_date(as_of)
    title = f'Purchase Order report {file_date}'
    unsub = ''
    if unsubscribe_href:
        unsub = (
            f' · <a href="{unsubscribe_href}" style="color:#666;">'
            f'Unsubscribe</a>'
        )
    listing = _list_html(pos or [])
    return f'''<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8" /><title>{title}</title></head>
<body style="margin:0;padding:0;background:#e8eaed;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#e8eaed;padding:16px 8px;">
    <tr><td align="center">
      <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;background:#ffffff;border:1px solid #c5c9d0;">
        <tr>
          <td style="padding:12px 16px;background:#e87722;border-bottom:3px solid #c45f12;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="vertical-align:middle;width:128px;">
                  <img src="cid:gazebo-logo" alt="Gazeboo Cloud" width="120" height="74"
                       style="display:block;border:0;width:120px;height:auto;" />
                </td>
                <td style="vertical-align:middle;padding-left:12px;color:#000000;">
                  <div style="font-size:18px;font-weight:bold;letter-spacing:0.2px;color:#000000;">{title}</div>
                  <div style="font-size:12px;color:#000000;margin-top:2px;">Gazeboo Cloud</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:14px 16px;font-size:14px;color:#222;line-height:1.4;">
            <p style="margin:0 0 8px;">Good Morning,</p>
            {listing}
            <p style="margin:10px 0 4px;">Wish you good day ahead</p>
            <p style="margin:0;">Kind Regards</p>
          </td>
        </tr>
        <tr>
          <td style="padding:8px 16px;background:#f4f5f7;border-top:1px solid #d0d4db;font-size:11px;color:#666;">
            Sent by Gazeboo Cloud · Do not reply to this message{unsub}
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>'''


def send_open_po_reminder(
    *,
    as_of: date | None = None,
    dry_run: bool = False,
) -> dict:
    day = as_of or timezone.localdate()
    title = f'Purchase Order report {report_file_date(day)}'
    recipients = active_recipients(StockReportEmailRecipient.REPORT_OPEN_PO)
    rows = open_po_reminder_rows(day)
    pos = consolidated_po_rows(rows)
    csv_bytes = rows_to_csv(rows)
    filename = f'open-purchase-orders-{day.isoformat()}.csv'
    emails = [row.email for row in recipients]
    list_text = _list_text(pos)

    result = {
        'as_of': day.isoformat(),
        'row_count': len(rows),
        'po_count': len(pos),
        'recipients': emails,
        'message_id': None,
        'message_ids': [],
        'skipped': False,
        'filename': filename,
        'csv_bytes': len(csv_bytes),
    }

    if not recipients:
        result['skipped'] = True
        return result

    if dry_run:
        return result

    message_ids: list[str] = []
    try:
        for row in recipients:
            href = unsubscribe_url(row.id)
            body_html = build_open_po_html(
                as_of=day, pos=pos, unsubscribe_href=href,
            )
            message_id = send_email_with_attachment(
                to_addresses=[row.email],
                subject=f'Gazeboo Cloud — {title}',
                body_text=(
                    f'{title}\n\n'
                    'Good Morning,\n\n'
                    f'{list_text}\n'
                    'Wish you good day ahead !\n\n'
                    'Kind Regards\n'
                    'Team Gazeboo Cloud\n\n'
                    f'To stop these emails: {href}\n'
                ),
                body_html=body_html,
                filename=filename,
                content=csv_bytes,
            )
            message_ids.append(message_id)
    except SesMailError:
        raise

    result['message_ids'] = message_ids
    result['message_id'] = message_ids[-1] if message_ids else None
    return result
