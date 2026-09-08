"""Read-only one-product warehouse dossier + briefing."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from product.models import Product
from product.query import active_products
from stock_ledger.models import (
    StockBalance,
    StockEntry,
    StockEntryLabelStatus,
    StockEntryPostingStatus,
    StockEntryType,
    StockFifoOverride,
)
from stock_ledger.util import entry_labels, entry_posting, scan, stickers
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.serialize import _pretty_qty, actor_names_for

UK = ZoneInfo('Europe/London')
_DRAW_TYPES = (StockEntryType.TRANSFER_OUT, StockEntryType.ISSUE)
_ADHOC = 'goods_out_adhoc'
_LEDGER_LIMIT = 200
_RELATED = (
    'destination_container',
    'source_container',
    'unit',
)


class InvestigateError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _qty(value, unit: str | None) -> str:
    text = _pretty_qty(Decimal(str(value)))
    return f'{text} {unit}' if unit else text


def _aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.utc)
    return dt


def _uk(dt):
    dt = _aware(dt)
    return dt.astimezone(UK) if dt is not None else None


def _clock(dt) -> str:
    local = _uk(dt)
    return local.strftime('%H:%M') if local else ''


def _day_label(dt) -> str:
    local = _uk(dt)
    if local is None:
        return ''
    return f'{local.strftime("%a")} {local.day} {local.strftime("%b")}'


def _short_date(dt) -> str:
    local = _uk(dt)
    return f'{local.day} {local.strftime("%b")}' if local else ''


def _day_window(day: date):
    start = datetime.combine(day, time.min, tzinfo=UK)
    return start, start + timedelta(days=1)


def _parse_day(raw) -> date:
    if raw in (None, ''):
        return timezone.now().astimezone(UK).date()
    parsed = parse_date(str(raw).strip())
    if parsed is None:
        raise InvestigateError('date must be YYYY-MM-DD.')
    return parsed


def _parse_int(raw, name: str) -> int | None:
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise InvestigateError(f'{name} must be an integer.') from exc


def _resolve_product_and_bag(*, code, recipe_code, product_id, q):
    bag = None
    product = None
    text = (code or '').strip()
    if text:
        try:
            match = scan.resolve_scan(text)
        except StockValidationError as exc:
            product = (
                Product.objects
                .select_related(*_RELATED)
                .filter(recipe_code__iexact=text)
                .first()
                or active_products(q=text).first()
            )
            if product is None:
                msg = str(exc)
                raise InvestigateError(
                    msg, 404 if 'not found' in msg.lower() or 'void' in msg.lower() else 400,
                ) from exc
        else:
            product = match['product']
            entry = match.get('entry')
            if entry is not None:
                if (
                    entry.entry_type in _DRAW_TYPES
                    and entry.source_entry_id
                ):
                    bag = entry.source_entry
                else:
                    bag = entry
    if product is None and recipe_code:
        product = (
            Product.objects
            .select_related(*_RELATED)
            .filter(recipe_code__iexact=str(recipe_code).strip())
            .first()
        )
    pid = _parse_int(product_id, 'product_id')
    if product is None and pid is not None:
        product = (
            Product.objects
            .select_related(*_RELATED)
            .filter(pk=pid)
            .first()
        )
    if product is None and q:
        product = active_products(q=str(q).strip()).first()
    if product is None:
        if pid is not None or recipe_code:
            raise InvestigateError('Product not found.', status_code=404)
        raise InvestigateError(
            'Pass code, recipe_code, product_id, or q.',
            status_code=400,
        )
    return product, bag


def _posted_receipts(product_id: int):
    return list(
        StockEntry.objects
        .filter(
            lot__product_id=product_id,
            entry_type=StockEntryType.RECEIPT,
        )
        .filter(
            Q(posting__isnull=True)
            | Q(posting__status=StockEntryPostingStatus.POSTED)
        )
        .select_related('lot', 'location', 'unit', 'posting')
        .order_by('id')
    )


def _draws_for_product(product_id: int):
    return list(
        StockEntry.objects
        .filter(
            lot__product_id=product_id,
            entry_type__in=_DRAW_TYPES,
            source_entry_id__isnull=False,
        )
        .select_related(
            'source_entry__lot',
            'source_entry__location',
            'source_entry__unit',
            'location',
            'counterparty_location',
            'posting',
            'label',
            'unit',
        )
        .order_by('id')
    )


def _in_window(dt, start, end) -> bool:
    dt = _aware(dt)
    return dt is not None and start <= dt < end


def _pick_focus(bag, receipts, day_draws):
    if bag is not None:
        return bag
    if day_draws:
        return day_draws[-1].source_entry
    empty = [
        r for r in receipts
        if stickers.remaining_for_entry(r) <= 0
    ]
    if empty:
        return empty[-1]
    if receipts:
        return receipts[0]
    return None


def _balances(product_id: int) -> list[dict]:
    rows = []
    qs = (
        StockBalance.objects
        .filter(lot__product_id=product_id)
        .select_related('location', 'lot')
        .order_by('location_id', 'lot_id')
    )
    for b in qs:
        rows.append({
            'lot_id': b.lot_id,
            'trace_number': b.lot.trace_number,
            'location_id': b.location_id,
            'location_name': b.location.name if b.location_id else None,
            'quantity': _pretty_qty(b.quantity),
        })
    return rows


def _warehouse_qty(product_id: int) -> Decimal:
    total = (
        StockBalance.objects
        .filter(lot__product_id=product_id)
        .aggregate(total=Sum('quantity'))
        ['total']
    )
    return total if total is not None else Decimal('0')


def _loc_qty(product_id: int, location_id: int | None) -> Decimal:
    if location_id is None:
        return _warehouse_qty(product_id)
    total = (
        StockBalance.objects
        .filter(lot__product_id=product_id, location_id=location_id)
        .aggregate(total=Sum('quantity'))
        ['total']
    )
    return total if total is not None else Decimal('0')


def _actor(draw) -> str:
    posting = entry_posting.get_posting(draw)
    uid = None
    lan = draw.lan_username
    if posting is not None:
        uid = posting.actor_user_id
        lan = posting.lan_username or lan
        meta = posting.meta if isinstance(posting.meta, dict) else {}
        mr = meta.get('manager_remove') if isinstance(meta, dict) else None
        if isinstance(mr, dict):
            uid = mr.get('actor_user_id') or uid
            lan = mr.get('lan_username') or lan
    if uid is not None:
        names = actor_names_for({uid})
        if names.get(uid):
            return names[uid]
    return lan or 'Unknown'


def _reason(entry) -> str | None:
    posting = entry_posting.get_posting(entry)
    if posting is not None:
        meta = posting.meta if isinstance(posting.meta, dict) else {}
        mr = meta.get('manager_remove') if isinstance(meta, dict) else None
        if isinstance(mr, dict) and mr.get('reason'):
            return str(mr['reason']).strip() or None
    try:
        fo = entry.fifo_override
    except StockFifoOverride.DoesNotExist:
        fo = None
    if fo is not None and (fo.reason or '').strip():
        return fo.reason.strip()
    if (entry.override_reason or '').strip():
        return entry.override_reason.strip()
    if (entry.remarks or '').strip():
        return entry.remarks.strip()
    return None


def _when_label(dt) -> str:
    local = _uk(dt)
    if local is None:
        return ''
    return f'{local.day} {local.strftime("%b")} {local.strftime("%H:%M")}'


def _product_entries(product_id: int) -> list:
    rows = list(
        StockEntry.objects
        .filter(lot__product_id=product_id)
        .select_related(
            'lot',
            'location',
            'counterparty_location',
            'unit',
            'posting',
            'label',
            'source_entry',
            'reverses_entry',
            'reversed_by',
            'fifo_override',
        )
        .order_by('-id')[:_LEDGER_LIMIT]
    )
    rows.reverse()
    return rows


def _how(entry, status, posting) -> str:
    if (
        posting is not None
        and isinstance(posting.meta, dict)
        and posting.meta.get('manager_remove')
    ):
        return 'manager_remove'
    try:
        if entry.fifo_override is not None:
            return 'fifo_override'
    except StockFifoOverride.DoesNotExist:
        pass
    if entry.entry_type == StockEntryType.REVERSAL:
        return 'reversal'
    if status == StockEntryPostingStatus.CANCELLED:
        return 'cancel'
    return status or entry.entry_type


def _ledger_row(entry, unit: str | None) -> dict:
    posting, queued_at, posted_at, cancelled_at, status = _draw_times(entry)
    when = posted_at or cancelled_at or queued_at or entry.recorded_at
    src = entry.location.name if entry.location_id else None
    dest = (
        entry.counterparty_location.name
        if entry.counterparty_location_id
        else None
    )
    try:
        rev = entry.reversed_by
    except StockEntry.DoesNotExist:
        rev = None
    reason = _reason(entry) or (_reason(rev) if rev is not None else None)
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'entry_type': entry.entry_type,
        'status': status,
        'quantity': _pretty_qty(abs(entry.quantity)),
        'unit': unit,
        'from_location': src,
        'to_location': dest,
        'source_bag': (
            entry_labels.entry_code(entry.source_entry_id)
            if entry.source_entry_id else None
        ),
        'reverses': (
            entry_labels.entry_code(entry.reverses_entry_id)
            if entry.reverses_entry_id else None
        ),
        'reversed_by': (
            entry_labels.entry_code(rev.id) if rev is not None else None
        ),
        'reversed_by_actor': _actor(rev) if rev is not None else None,
        'source_document_type': entry.source_document_type,
        'trace_number': entry.lot.trace_number if entry.lot_id else None,
        'at': when.isoformat() if when else None,
        'when_label': _when_label(when),
        'queued_at': queued_at.isoformat() if queued_at else None,
        'posted_at': posted_at.isoformat() if posted_at else None,
        'cancelled_at': cancelled_at.isoformat() if cancelled_at else None,
        'actor': _actor(entry),
        'reason': reason,
        'how': _how(entry, status, posting),
    }


def _split_ledger(entries, unit: str | None) -> dict:
    goods_in, goods_out, transfer_in, reversals, cancelled = [], [], [], [], []
    for entry in entries:
        row = _ledger_row(entry, unit)
        if entry.entry_type == StockEntryType.RECEIPT:
            goods_in.append(row)
        elif entry.entry_type in _DRAW_TYPES:
            goods_out.append(row)
            if row['status'] == StockEntryPostingStatus.CANCELLED:
                cancelled.append(row)
        elif entry.entry_type == StockEntryType.TRANSFER_IN:
            transfer_in.append(row)
        elif entry.entry_type == StockEntryType.REVERSAL:
            reversals.append(row)
        if (
            row['reversed_by']
            and entry.entry_type != StockEntryType.REVERSAL
            and row not in reversals
            and row not in cancelled
        ):
            reversals.append(row)
    return {
        'goods_in': goods_in,
        'goods_out': goods_out,
        'transfer_in': transfer_in,
        'reversals': reversals,
        'cancelled': cancelled,
    }


def _line(row: dict) -> str:
    qty = f'{row["quantity"]} {row["unit"]}' if row.get('unit') else row['quantity']
    route = ''
    if row.get('from_location') and row.get('to_location'):
        route = f' {row["from_location"]} → {row["to_location"]}'
    elif row.get('from_location'):
        route = f' at {row["from_location"]}'
    src = f' from {row["source_bag"]}' if row.get('source_bag') else ''
    rev = ''
    if row.get('reverses'):
        rev = f' reverses {row["reverses"]}'
    elif row.get('reversed_by'):
        who = row.get('reversed_by_actor') or ''
        rev = f' reversed by {row["reversed_by"]}'
        if who:
            rev += f' ({who})'
    reason = row.get('reason') or 'not recorded'
    clock = row.get('when_label') or ''
    when = f' {clock}' if clock else ''
    left = (
        f' remaining {row["remaining"]}'
        if row.get('remaining') is not None else ''
    )
    return (
        f'- {row["entry_code"]} {qty}{route}{src}{rev}{left}'
        f' {row["status"]}{when} by {row["actor"]}'
        f' ({row["how"]}). Reason: {reason}.'
    )


def _screen(draw) -> str:
    if draw.source_document_type == _ADHOC:
        return 'Goods out without plan'
    return 'Goods out'


def _draw_times(draw):
    posting = entry_posting.get_posting(draw)
    queued_at = posting.queued_at if posting else draw.recorded_at
    posted_at = posting.posted_at if posting else None
    cancelled_at = posting.cancelled_at if posting else None
    status = posting.status if posting else StockEntryPostingStatus.POSTED
    if posting is None:
        posted_at = draw.recorded_at
    return posting, queued_at, posted_at, cancelled_at, status


def _timeline_rows(day_draws, bag, unit: str | None) -> list[dict]:
    bag_code = entry_labels.entry_code(bag.id)
    bag_qty = _qty(abs(bag.quantity), unit)
    saw_cancel = False
    rows = []
    for draw in day_draws:
        posting, queued_at, posted_at, cancelled_at, status = _draw_times(draw)
        label = entry_labels.get_label(draw)
        qty = _qty(abs(draw.quantity), unit)
        src = draw.location.name if draw.location_id else 'this location'
        dest = (
            draw.counterparty_location.name
            if draw.counterparty_location_id
            else 'destination'
        )
        draw_code = entry_labels.entry_code(draw.id)
        what = (
            f'Scanned {bag_code} on {_screen(draw)} → queued {draw_code} '
            f'(full {qty} {src} → {dest}).'
        )
        if (
            label is not None
            and label.printed_at
            and status == StockEntryPostingStatus.CANCELLED
            and label.status != StockEntryLabelStatus.VERIFIED
        ):
            what += ' Printed GO label, never verified.'
        rows.append({'at': queued_at, 'until': None, 'what': what})
        full = abs(draw.quantity) >= abs(bag.quantity)
        if status == StockEntryPostingStatus.CANCELLED:
            if full:
                rows.append({
                    'at': queued_at,
                    'until': cancelled_at,
                    'what': (
                        f'That queue reserved the whole bag. Re-scan of '
                        f'{bag_code} = remaining 0 → “no stock in bag”.'
                    ),
                })
            rows.append({
                'at': cancelled_at,
                'until': None,
                'what': f'Cancelled {draw_code}. Bag qty back to {bag_qty}.',
            })
            saw_cancel = True
        elif status == StockEntryPostingStatus.POSTED:
            secs = ''
            if queued_at and posted_at:
                n = int((posted_at - queued_at).total_seconds())
                if n >= 0:
                    secs = f' in {n}s'
            lead = 'Did it again: ' if saw_cancel else ''
            rows.append({
                'at': posted_at or queued_at,
                'until': None,
                'what': (
                    f'{lead}{draw_code} printed, verified, posted{secs}. '
                    f'{qty} moved to {dest}.'
                ),
            })
        elif status == StockEntryPostingStatus.QUEUED and full:
            rows.append({
                'at': queued_at,
                'until': None,
                'what': (
                    f'That queue reserved the whole bag. Re-scan of '
                    f'{bag_code} = remaining 0 → “no stock in bag”.'
                ),
            })
    return rows


def _fmt_when(row: dict) -> str:
    start = _clock(row['at'])
    until = row.get('until')
    if until:
        return f'{start}–{_clock(until)}'
    return start


def _next_codes(receipts, bag, location_id: int | None) -> list[str]:
    codes = []
    for receipt in receipts:
        if bag is not None and receipt.id == bag.id:
            continue
        if location_id is not None and receipt.location_id != location_id:
            continue
        if stickers.remaining_for_entry(receipt) <= 0:
            continue
        codes.append(entry_labels.entry_code(receipt.id))
    return codes


def _decision(
    *,
    bag,
    remaining: Decimal,
    queued,
    loc_qty: Decimal,
    warehouse: Decimal,
    next_codes: list[str],
):
    if remaining <= 0 and queued:
        kind = 'reserved_by_queue'
        queued_code = entry_labels.entry_code(queued[0].id)
    elif remaining <= 0:
        kind = 'bag_empty'
        queued_code = None
    else:
        kind = 'bag_has_stock'
        queued_code = None
    return {
        'kind': kind,
        'bag_remaining': _pretty_qty(remaining),
        'lot_qty_here': _pretty_qty(loc_qty),
        'warehouse_qty': _pretty_qty(warehouse),
        'queued_draw_code': queued_code,
        'use_next_bag': next_codes[:8],
    }


def _section(title: str, rows: list[dict]) -> list[str]:
    if not rows:
        return [f'## {title}', '(none)', '']
    return [f'## {title}', *(_line(r) for r in rows), '']


def _briefing(
    *,
    bag,
    product,
    remaining: Decimal,
    loc_qty: Decimal,
    warehouse: Decimal,
    loc_name: str,
    unit: str | None,
    day_draws,
    timeline: list[dict],
    next_codes: list[str],
    dest_name: str | None,
    ledger: dict,
    balances: list[dict],
) -> str:
    code = entry_labels.entry_code(bag.id)
    name = product.name
    if remaining <= 0 and warehouse > 0:
        headline = f'{code} is empty now. Warehouse {name} is not missing.'
    elif remaining <= 0:
        headline = f'{code} is empty. Warehouse {name} has no stock.'
    else:
        headline = f'{code} still has {_qty(remaining, unit)}.'

    posted_at = bag.recorded_at
    posting = entry_posting.get_posting(bag)
    if posting is not None and posting.posted_at:
        posted_at = posting.posted_at
    recipe = product.recipe_code or ''
    lot = bag.lot.trace_number if bag.lot_id else ''
    src = product.source_container
    src_name = src.name if src is not None else None
    bag_line = (
        f'Bag: {code} = {_qty(abs(bag.quantity), unit)} {name}'
        f' ({recipe}, lot {lot}) at {loc_name}, posted {_short_date(posted_at)}.'
    )
    on_hand = [
        f'{row["quantity"]} {unit or ""} at {row["location_name"]} '
        f'(lot {row["trace_number"]})'.strip()
        for row in balances
        if Decimal(row['quantity']) != 0
    ] or ['0']
    parts = [
        headline,
        '',
        '## Product',
        f'{name} ({recipe or "no recipe code"}). '
        f'Source {src_name or "—"}. Dest {dest_name or "—"}. '
        f'GI type {product.goods_in_type or "—"}.',
        '',
        '## On hand',
        '; '.join(on_hand),
        '',
        *_section('Goods in', ledger.get('goods_in') or []),
        *_section('Goods out', ledger.get('goods_out') or []),
        *_section(
            'Cancelled / reversed',
            list({
                r['entry_id']: r
                for r in (ledger.get('cancelled') or [])
                + (ledger.get('reversals') or [])
            }.values()),
        ),
        bag_line,
    ]
    if day_draws:
        who = _actor(day_draws[0])
        parts.append('')
        parts.append(f'{_day_label(day_draws[0].recorded_at)} ({who}):')
        parts.append('')
        parts.append('Time | What')
        for row in timeline:
            parts.append(f'{_fmt_when(row)} | {row["what"]}')

    adhoc = any(d.source_document_type == _ADHOC for d in day_draws)
    if adhoc and dest_name:
        parts.append('')
        parts.append(
            f'Why without plan: product dest is {dest_name}. '
            f'Scan on that screen always starts goods_out_adhoc. '
            f'Working as designed.'
        )

    if remaining <= 0:
        other = (
            f'Same lot still has {_qty(loc_qty, unit)} at {loc_name}'
            if loc_qty > 0
            else f'No stock left at {loc_name}'
        )
        rest = []
        seen = {loc_name}
        for row in _balances(product.id):
            q = Decimal(row['quantity'])
            loc = row['location_name']
            if q > 0 and loc not in seen:
                rest.append(f'{_qty(q, unit)} at {loc}')
                seen.add(loc)
        extra = f' and {", ".join(rest)}' if rest else ''
        reserved = any(
            entry_posting.get_posting(d)
            and entry_posting.get_posting(d).status
            == StockEntryPostingStatus.QUEUED
            for d in day_draws
        )
        why = (
            'the bag is fully reserved by a queued pick'
            if reserved
            else 'the sticker was fully issued'
        )
        parts.append('')
        parts.append(
            f'Why “no stock in bag”: not a stock hole. {why[0].upper()}{why[1:]}. '
            f'{other}{extra}.'
        )
        nxt = ', '.join(next_codes[:8]) if next_codes else 'another posted bag'
        if len(next_codes) > 8:
            nxt = f'{nxt}, …'
        parts.append('')
        parts.append(
            f'Scanning {code} now still says no stock — that bag is gone. '
            f'Use another {loc_name} bag ({nxt}).'
        )
    return '\n'.join(parts)


def investigate(
    *,
    code=None,
    recipe_code=None,
    product_id=None,
    q=None,
    date=None,
    location_id=None,
) -> dict:
    product, bag = _resolve_product_and_bag(
        code=code,
        recipe_code=recipe_code,
        product_id=product_id,
        q=q,
    )
    day = _parse_day(date)
    loc_id = _parse_int(location_id, 'location_id')
    start, end = _day_window(day)
    receipts = _posted_receipts(product.id)
    draws = _draws_for_product(product.id)
    day_all = [
        d for d in draws
        if _in_window(_draw_times(d)[1], start, end)
        or _in_window(d.recorded_at, start, end)
        or _in_window(_draw_times(d)[2], start, end)
        or _in_window(_draw_times(d)[3], start, end)
    ]
    bag = _pick_focus(bag, receipts, day_all)
    if bag is None:
        raise InvestigateError(
            'No goods-in sticker found for this product.',
            status_code=404,
        )
    if loc_id is None:
        loc_id = bag.location_id
    day_draws = [d for d in day_all if d.source_entry_id == bag.id]
    remaining = stickers.remaining_for_entry(bag)
    queued = [
        d for d in day_draws
        if (_draw_times(d)[4] == StockEntryPostingStatus.QUEUED)
    ]
    unit = (
        bag.unit.name if bag.unit_id
        else (product.unit.name if product.unit_id else None)
    )
    loc_name = bag.location.name if bag.location_id else 'this location'
    loc_qty = _loc_qty(product.id, loc_id)
    warehouse = _warehouse_qty(product.id)
    next_codes = _next_codes(receipts, bag, loc_id)
    timeline = _timeline_rows(day_draws, bag, unit)
    dest = product.destination_container
    dest_name = dest.name if dest is not None else None
    src = product.source_container
    src_name = src.name if src is not None else None
    ledger = _split_ledger(_product_entries(product.id), unit)
    remain_map = {
        receipt.id: _pretty_qty(stickers.remaining_for_entry(receipt))
        for receipt in receipts
    }
    for row in ledger['goods_in']:
        if row['entry_id'] in remain_map:
            row['remaining'] = remain_map[row['entry_id']]
    stickers_out = []
    for receipt in receipts:
        stickers_out.append({
            'entry_id': receipt.id,
            'entry_code': entry_labels.entry_code(receipt.id),
            'remaining': _pretty_qty(stickers.remaining_for_entry(receipt)),
            'quantity': _pretty_qty(abs(receipt.quantity)),
            'location_id': receipt.location_id,
            'location_name': (
                receipt.location.name if receipt.location_id else None
            ),
        })
    events = []
    for draw in day_draws:
        posting, queued_at, posted_at, cancelled_at, status = _draw_times(draw)
        events.append({
            'entry_id': draw.id,
            'entry_code': entry_labels.entry_code(draw.id),
            'entry_type': draw.entry_type,
            'status': status,
            'quantity': _pretty_qty(abs(draw.quantity)),
            'source_document_type': draw.source_document_type,
            'from_location': (
                draw.location.name if draw.location_id else None
            ),
            'to_location': (
                draw.counterparty_location.name
                if draw.counterparty_location_id
                else None
            ),
            'queued_at': queued_at.isoformat() if queued_at else None,
            'posted_at': posted_at.isoformat() if posted_at else None,
            'cancelled_at': (
                cancelled_at.isoformat() if cancelled_at else None
            ),
            'actor': _actor(draw),
        })
    decision = _decision(
        bag=bag,
        remaining=remaining,
        queued=queued,
        loc_qty=loc_qty,
        warehouse=warehouse,
        next_codes=next_codes,
    )
    balances = _balances(product.id)
    return {
        'product': {
            'product_id': product.id,
            'name': product.name,
            'recipe_code': product.recipe_code,
            'gff_code': product.gff_code,
            'goods_in_type': product.goods_in_type,
            'source_container_id': product.source_container_id,
            'source_container_name': src_name,
            'destination_container_id': product.destination_container_id,
            'destination_container_name': dest_name,
            'unit': unit,
        },
        'bag': {
            'entry_id': bag.id,
            'entry_code': entry_labels.entry_code(bag.id),
            'quantity': _pretty_qty(abs(bag.quantity)),
            'remaining': _pretty_qty(remaining),
            'location_id': bag.location_id,
            'location_name': loc_name,
            'trace_number': bag.lot.trace_number if bag.lot_id else None,
        },
        'date': day.isoformat(),
        'stickers': stickers_out,
        'events': events,
        'goods_in': ledger['goods_in'],
        'goods_out': ledger['goods_out'],
        'transfer_in': ledger['transfer_in'],
        'cancelled': ledger['cancelled'],
        'reversals': ledger['reversals'],
        'balances': balances,
        'decision': decision,
        'timeline': [
            {
                'at': _clock(r['at']),
                'until': _clock(r['until']) if r.get('until') else None,
                'what': r['what'],
            }
            for r in timeline
        ],
        'briefing': _briefing(
            bag=bag,
            product=product,
            remaining=remaining,
            loc_qty=loc_qty,
            warehouse=warehouse,
            loc_name=loc_name,
            unit=unit,
            day_draws=day_draws,
            timeline=timeline,
            next_codes=next_codes,
            dest_name=dest_name,
            ledger=ledger,
            balances=balances,
        ),
    }
