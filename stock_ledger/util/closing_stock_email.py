"""Build and email the daily closing-stock CSV report (HTML + CSV)."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.signing import BadSignature, Signer
from django.utils import timezone

from stock_ledger.models import StockReportEmailRecipient
from stock_ledger.util.reports import closing_balances_as_of
from stock_ledger.util.ses_mail import SesMailError, send_email_with_attachment

_UNSUB_SALT = 'stock-report-email-unsub'

_CSV_FIELDS = (
    'as_of',
    'product_id',
    'product_name',
    'recipe_code',
    'gff_code',
    'goods_in_type',
    'lot_id',
    'trace_number',
    'use_by',
    'location_id',
    'location_name',
    'unit_id',
    'unit_name',
    'quantity',
    'quantity_base',
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


def rows_to_csv(rows: list[dict]) -> bytes:
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
    del rows  # CSV carries the detail; body is a short note only.
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
    Email closing stock (HTML + CSV) to each active recipient (per-person unsub link).
    Returns {as_of, row_count, recipients, message_ids, skipped}.
    """
    day = as_of or default_as_of()
    day_label = format_report_date(day)
    recipients = active_recipients()
    rows = closing_balances_as_of(as_of=day)
    csv_bytes = rows_to_csv(rows)
    filename = f'closing-stock-{day.isoformat()}.csv'
    emails = [row.email for row in recipients]

    result = {
        'as_of': day.isoformat(),
        'row_count': len(rows),
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
            body_html = build_closing_stock_html(as_of=day, unsubscribe_href=href)
            message_id = send_email_with_attachment(
                to_addresses=[row.email],
                subject=f'Gazeboo Cloud — Closing stock as of {day_label}',
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
                content=csv_bytes,
            )
            message_ids.append(message_id)
    except SesMailError:
        raise

    result['message_ids'] = message_ids
    result['message_id'] = message_ids[-1] if message_ids else None
    return result
