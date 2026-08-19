from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from stock_ledger.util.conversions import StockValidationError


def parse_decimal(value, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid decimal for {field_name}.') from exc


def parse_effective_at(value):
    if value in (None, ''):
        return timezone.now()
    dt = parse_datetime(str(value))
    if dt is None:
        raise ValueError('Invalid effective_at. Use ISO-8601 datetime.')
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def parse_date(value, field_name: str):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f'Invalid date for {field_name}. Use YYYY-MM-DD.') from exc


def format_display_date(value) -> str | None:
    """YYYY-MM-DD / date → '03 Aug 2026' for UI lists."""
    if value in (None, ''):
        return None
    if isinstance(value, date):
        d = value
    else:
        try:
            d = date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    return d.strftime('%d %b %Y')


def optional_int_param(raw, field_name: str):
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be an integer.') from exc


def optional_unit_id(body: dict) -> int | None:
    raw = body.get('unit_id')
    if raw in (None, ''):
        return None
    return int(raw)


def parse_unit_moves(body: dict) -> list | None:
    raw = body.get('unit_moves')
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise StockValidationError('unit_moves must be a list')
    return raw


def parse_requirement_ids(raw) -> list[int] | None:
    if raw in (None, ''):
        return None
    if not isinstance(raw, list):
        raise StockValidationError('requirement_ids must be a list.')
    if not raw:
        return None
    try:
        return [int(x) for x in raw]
    except (TypeError, ValueError) as exc:
        raise StockValidationError('requirement_ids must be integers.') from exc
