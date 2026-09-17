"""Build and email the daily closing-stock report (HTML + Excel)."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django.conf import settings
from django.core.signing import BadSignature, Signer
from django.utils import timezone
from openpyxl import Workbook

from stock_ledger.models import StockReportEmailRecipient
from stock_ledger.util.reports import closing_balances_as_of
from stock_ledger.util.ses_mail import SesMailError, send_email_with_attachment

_UNSUB_SALT = 'stock-report-email-unsub'

_XLSX_CONTENT_TYPE = (
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
)

# Match FE export: docs/closing-stock-repor/Closing stock detail - *.xlsx
_XLSX_HEADERS = (
    'Product Code',
    'Sage Product Code',
    'Product Name',
    'Trace Number',
    'Use by / best before',
    'Production date',
    'Location',
    'Pack Shape Format',
    'Packs Qty',
    'Packs Unit',
    'Stock Qty',
    'Stock Unit',
    'Stock Qty (kg)',
    'Stock Unit (kg)',
)

_CSV_FIELDS = (
    'as_of',
    'product_id',
    'product_name',
    'recipe_code',
    'gff_code',
    'goods_in_type',
    'lot_id',
    'trace_number',
    'production_date',
    'use_by',
    'location_id',
    'location_name',
    'unit_id',
    'unit_name',
    'quantity',
    'quantity_base',
    'supplier_code',
    'sage_product_code',
    'shape_format_label',
    'pack_quantity',
    'pack_unit_name',
    'display_kg',
)


def default_as_of() -> date:
    return timezone.localdate() - timedelta(days=1)


def format_report_date(value: date) -> str:
    """e.g. 25 Sep 2026"""
    return f"{value.day} {value.strftime('%b %Y')}"


def unsubscribe_token(recipient_id: int) -> str:
    return Signer(salt=_UNSUB_SALT).sign(str(recipient_id))


def recipient_id_from_unsubscribe_token(token: str) -> int:
    try:
        return int(Signer(salt=_UNSUB_SALT).unsign(token))
    except (BadSignature, TypeError, ValueError) as exc:
        raise ValueError('Invalid or expired unsubscribe link.') from exc


def unsubscribe_url(recipient_id: int) -> str:
    base = getattr(settings, 'PUBLIC_API_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
    token = quote(unsubscribe_token(recipient_id), safe='')
    return f'{base}/stock/reports/email-unsubscribe/?token={token}'


def _cell(value) -> str | int | float | None:
    if value is None or value == '':
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def _to_decimal(value) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _excel_data_row(row: dict) -> tuple:
    """Map API closing-stock row → FE Excel columns."""
    display_kg = _to_decimal(row.get('display_kg'))
    if display_kg is not None:
        stock_qty = display_kg * Decimal('1000')
        stock_unit = 'grams'
        stock_qty_kg = display_kg
        stock_unit_kg = 'kg'
    else:
        stock_qty = _to_decimal(row.get('quantity'))
        stock_unit = row.get('unit_name')
        stock_qty_kg = None
        stock_unit_kg = None

    product_code = row.get('recipe_code') or row.get('gff_code')
    return (
        _cell(product_code),
        _cell(row.get('sage_product_code')),
        _cell(row.get('product_name')),
        _cell(row.get('trace_number')),
        _cell(row.get('use_by')),
        _cell(row.get('production_date')),
        _cell(row.get('location_name')),
        _cell(row.get('shape_format_label')),
        _cell(row.get('pack_quantity')),
        _cell(row.get('pack_unit_name')),
        _cell(stock_qty),
        _cell(stock_unit),
        _cell(stock_qty_kg),
        _cell(stock_unit_kg),
    )


def rows_to_xlsx(rows: list[dict], *, as_of: date) -> bytes:
    """FE-matching workbook: Data sheet + Meta sheet."""
    wb = Workbook()
    data = wb.active
    data.title = 'Data'
    data.append(['Closing stock detail'])
    data.append(list(_XLSX_HEADERS))
    for row in rows:
        data.append(list(_excel_data_row(row)))

    meta = wb.create_sheet('Meta')
    meta.append(['Field', 'Value'])
    meta.append(['Brand', 'www.gazeboo.cloud'])
    meta.append(['Report', 'Closing stock'])
    meta.append(['Downloaded by', 'System Admin'])
    meta.append(['Downloaded at (local)', timezone.localtime().isoformat()])
    meta.append(['Timezone', str(timezone.get_current_timezone())])
    meta.append(['Row count', len(rows)])
    meta.append([
        'Filters',
        f'As of: {format_report_date(as_of)}; View: Detailed',
    ])
    meta.append(['View', 'detail'])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def rows_to_csv(rows: list[dict]) -> bytes:
    """Legacy CSV helper (tests / ad-hoc). Prefer rows_to_xlsx for email."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k) if row.get(k) is not None else '' for k in _CSV_FIELDS})
    return buf.getvalue().encode('utf-8')


def build_closing_stock_html(
    *,
    as_of: date,
    rows: list[dict] | None = None,
    unsubscribe_href: str | None = None,
) -> str:
    """Branded Gazeboo Cloud HTML body. Logo via cid:gazebo-logo. No item list."""
    del rows  # Excel carries the detail; body is a short note only.
    day_label = format_report_date(as_of)
    unsub = ''
    if unsubscribe_href:
        unsub = (
            f' · <a href="{unsubscribe_href}" style="color:#666;">'
            f'Unsubscribe</a>'
        )
    return f'''<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8" /><title>Closing stock as of {day_label}</title></head>
<body style="margin:0;padding:0;background:#e8eaed;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#e8eaed;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;background:#ffffff;border:1px solid #c5c9d0;">
        <tr>
          <td style="padding:20px 24px;background:#e87722;border-bottom:3px solid #c45f12;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="vertical-align:middle;">
                  <img src="cid:gazebo-logo" alt="Gazeboo Cloud" width="120" height="74"
                       style="display:block;border:0;width:120px;height:auto;" />
                </td>
                <td style="vertical-align:middle;text-align:right;color:#000000;">
                  <div style="font-size:18px;font-weight:bold;letter-spacing:0.3px;color:#000000;">Gazeboo Cloud</div>
                  <div style="font-size:12px;color:#000000;margin-top:4px;">Stock reports</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:28px 24px;font-size:15px;color:#222;line-height:1.55;">
            <p style="margin:0 0 16px;">Good Morning,</p>
            <p style="margin:0 0 16px;">
              Closing stock report is available to download from Stock Section
              or find attached report.
            </p>
            <p style="margin:0 0 16px;">
              Report date: <strong>{day_label}</strong>
            </p>
            <p style="margin:0 0 24px;">Wish you good day ahead</p>
            <p style="margin:0;">Kind Regards</p>
          </td>
        </tr>
        <tr>
          <td style="padding:14px 24px;background:#f4f5f7;border-top:1px solid #d0d4db;font-size:11px;color:#666;">
            Sent by Gazeboo Cloud · Do not reply to this message{unsub}
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>'''


def active_recipients() -> list[StockReportEmailRecipient]:
    return list(
        StockReportEmailRecipient.objects.filter(is_active=True).order_by('email')
    )


def active_recipient_emails() -> list[str]:
    return [row.email for row in active_recipients()]


def send_closing_stock_report(
    *,
    as_of: date | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Email closing stock (HTML + Excel) to each active recipient (per-person unsub link).
    Returns {as_of, row_count, recipients, message_ids, skipped}.
    """
    day = as_of or default_as_of()
    day_label = format_report_date(day)
    recipients = active_recipients()
    rows = closing_balances_as_of(as_of=day)
    xlsx_bytes = rows_to_xlsx(rows, as_of=day)
    filename = f'Closing stock detail - {day.strftime("%d-%m-%Y")}.xlsx'
    emails = [row.email for row in recipients]

    result = {
        'as_of': day.isoformat(),
        'row_count': len(rows),
        'recipients': emails,
        'message_id': None,
        'message_ids': [],
        'skipped': False,
        'filename': filename,
        'attachment_bytes': len(xlsx_bytes),
        'csv_bytes': len(xlsx_bytes),  # backward-compatible key for callers/tests
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
            body_html = build_closing_stock_html(as_of=day, unsubscribe_href=href)
            message_id = send_email_with_attachment(
                to_addresses=[row.email],
                subject=f'Gazeboo Cloud - Closing stock as of {day_label}',
                body_text=(
                    'Good Morning,\n\n'
                    'Closing stock report is available to download from Stock Section '
                    'or find attached report.\n\n'
                    f'Report date: {day_label}\n\n'
                    'Wish you good day ahead !\n\n'
                    'Kind Regards\n'
                    'Team Gazeboo Cloud\n\n'
                    f'To stop these emails: {href}\n'
                ),
                body_html=body_html,
                filename=filename,
                content=xlsx_bytes,
                content_type=_XLSX_CONTENT_TYPE,
            )
            message_ids.append(message_id)
    except SesMailError:
        raise

    result['message_ids'] = message_ids
    result['message_id'] = message_ids[-1] if message_ids else None
    return result
