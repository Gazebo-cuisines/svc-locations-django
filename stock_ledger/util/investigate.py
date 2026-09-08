"""Read-only sticker/product incident pack + briefing (floor-complaint shape)."""

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
)
from stock_ledger.util import entry_labels, entry_posting, scan, stickers
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.serialize import _pretty_qty, actor_names_for

UK = ZoneInfo('Europe/London')
_DRAW_TYPES = (StockEntryType.TRANSFER_OUT, StockEntryType.ISSUE)
_ADHOC = 'goods_out_adhoc'


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
                .select_related('destination_container', 'unit')
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
            .select_related('destination_container', 'unit')
            .filter(recipe_code__iexact=str(recipe_code).strip())
            .first()
        )
    pid = _parse_int(product_id, 'product_id')
    if product is None and pid is not None:
        product = (
            Product.objects
            .select_related('destination_container', 'unit')
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
    if uid is not None:
        names = actor_names_for({uid})
        if names.get(uid):
            return names[uid]
    return lan or 'Unknown'


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
    bag_line = (
        f'Bag: {code} = {_qty(abs(bag.quantity), unit)} {name}'
        f' ({recipe}, lot {lot}) at {loc_name}, posted {_short_date(posted_at)}.'
    )

    parts = [headline, '', bag_line]
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
    return {
        'product': {
            'product_id': product.id,
            'name': product.name,
            'recipe_code': product.recipe_code,
            'destination_container_id': product.destination_container_id,
            'destination_container_name': dest_name,
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
        'balances': _balances(product.id),
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
        ),
    }
