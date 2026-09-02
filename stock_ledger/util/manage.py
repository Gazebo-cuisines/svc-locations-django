"""Stock Management screen: preview + cancel/reverse (no hard deletes)."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from stock_ledger.models import (
    StockEntry,
    StockEntryPostingStatus,
    StockEntryType,
    StockFifoOverride,
    StockUnit,
    StockUnitStatus,
)
from stock_ledger.util import entry_labels, entry_posting, stickers
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util import services
from stock_ledger.util.stock_units import void_unit
from users_rbac.models import RbacUser

RELATED = (
    'lot__product',
    'lot__shape_format',
    'unit',
    'location',
    'counterparty_location',
    'label',
    'posting',
    'fifo_override',
)


def _dec(value) -> str | None:
    if value is None:
        return None
    return format(Decimal(str(value)), 'f')


def _entry_code(entry_id: int | None) -> str:
    return entry_labels.entry_code(entry_id) if entry_id else ''


def _is_reversed(entry: StockEntry) -> bool:
    try:
        return entry.reversed_by is not None
    except StockEntry.DoesNotExist:
        return False


def _posting_status(entry: StockEntry) -> str | None:
    posting = entry_posting.get_posting(entry)
    return posting.status if posting is not None else None


def _actor_name(actor_user_id: int | None, lan_username: str | None) -> str | None:
    if actor_user_id:
        user = RbacUser.objects.filter(pk=actor_user_id).only(
            'display_name', 'username',
        ).first()
        if user:
            return user.display_name or user.username
    return lan_username or None


def _manage_entry_detail(entry: StockEntry, entry_dict_fn) -> dict:
    data = entry_dict_fn(entry)
    product = entry.lot.product if entry.lot_id else None
    qty = abs(entry.quantity) if entry.quantity is not None else None
    data.update(
        {
            'actor_name': _actor_name(entry.actor_user_id, entry.lan_username),
            'recorded_at': (
                entry.recorded_at.isoformat() if entry.recorded_at else None
            ),
            'device_serial': entry.device_serial,
            'recipe_code': getattr(product, 'recipe_code', None) if product else None,
            'gff_code': getattr(product, 'gff_code', None) if product else None,
            'goods_in_type': (
                getattr(product, 'goods_in_type', None) if product else None
            ),
            'quantity_abs': _dec(qty),
            'entry_unit_name': entry.unit.name if entry.unit_id else None,
            'quantity_base': _dec(entry.quantity_base),
            'display_grams': (
                _dec(Decimal(str(data['display_kg'])) * Decimal('1000'))
                if data.get('display_kg') not in (None, '')
                else None
            ),
            'lot_origin': entry.lot.origin if entry.lot_id else None,
            'entry_code': _entry_code(entry.id),
            'shape_outer_name': data.get('shape_outer_unit_name'),
            'shape_inner_name': data.get('shape_inner_unit_name'),
        }
    )
    return data


def _linked(entry: StockEntry, *, step: str | None = None) -> dict:
    return {
        'entry_id': entry.id,
        'entry_code': _entry_code(entry.id),
        'entry_type': entry.entry_type,
        'quantity': _dec(entry.quantity) or '',
        'step': step,
    }


def _linked_detail(entry: StockEntry, entry_dict_fn, *, step: str | None = None) -> dict:
    row = _linked(entry, step=step)
    row['entry'] = _manage_entry_detail(entry, entry_dict_fn)
    return row


def _load_entry(entry_id: int) -> StockEntry:
    entry = (
        StockEntry.objects
        .select_related(*RELATED)
        .filter(pk=entry_id)
        .first()
    )
    if entry is None:
        raise StockValidationError(f'entry_id={entry_id} not found')
    return entry


def _transfer_sibling(entry: StockEntry) -> StockEntry | None:
    if not entry.transfer_group_id:
        return None
    return (
        StockEntry.objects
        .select_related(*RELATED)
        .filter(transfer_group_id=entry.transfer_group_id)
        .exclude(pk=entry.pk)
        .exclude(entry_type=StockEntryType.REVERSAL)
        .order_by('id')
        .first()
    )


def _draws(entry: StockEntry):
    return (
        StockEntry.objects
        .select_related(*RELATED)
        .filter(source_entry_id=entry.pk)
        .exclude(entry_type=StockEntryType.COUNT_ADJUSTMENT)
        .exclude(entry_type=StockEntryType.REVERSAL)
        .order_by('id')
    )


def _stock_units(entry: StockEntry) -> list[StockUnit]:
    return list(
        StockUnit.objects
        .filter(created_by_entry_id=entry.pk)
        .order_by('id')
    )


def _preview_action(entry: StockEntry) -> dict:
    """Decide cancel / reverse / blocked / already_removed."""
    will_undo: list[dict] = []
    units_to_void: list[dict] = []

    if entry.entry_type == StockEntryType.REVERSAL:
        return {
            'action': 'already_removed',
            'block_reason': 'This row is a reversal.',
            'will_undo': will_undo,
            'units_to_void': units_to_void,
        }

    if _is_reversed(entry):
        return {
            'action': 'already_removed',
            'block_reason': 'Already reversed.',
            'will_undo': will_undo,
            'units_to_void': units_to_void,
        }

    posting = entry_posting.get_posting(entry)
    if posting is not None and posting.status == StockEntryPostingStatus.CANCELLED:
        return {
            'action': 'already_removed',
            'block_reason': 'Already cancelled.',
            'will_undo': will_undo,
            'units_to_void': units_to_void,
        }

    for draw in _draws(entry):
        if _is_reversed(draw):
            continue
        draw_posting = entry_posting.get_posting(draw)
        if draw_posting and draw_posting.status == StockEntryPostingStatus.CANCELLED:
            continue
        will_undo.append(_linked(draw, step='draw'))

    sibling = _transfer_sibling(entry)
    if sibling is not None and not _is_reversed(sibling):
        sib_posting = entry_posting.get_posting(sibling)
        if not (
            sib_posting
            and sib_posting.status == StockEntryPostingStatus.CANCELLED
        ):
            will_undo.append(_linked(sibling, step='transfer_sibling'))

    for unit in _stock_units(entry):
        if unit.status in (StockUnitStatus.ACTIVE, StockUnitStatus.PARTIALLY_CONSUMED):
            units_to_void.append(
                {'unit_serial': unit.unit_serial, 'status': unit.status},
            )
        elif unit.status == StockUnitStatus.CONSUMED:
            return {
                'action': 'blocked',
                'block_reason': (
                    f'Unit {unit.unit_serial} is fully consumed; '
                    'void is not allowed.'
                ),
                'will_undo': will_undo,
                'units_to_void': units_to_void,
            }

    if posting is not None and posting.status == StockEntryPostingStatus.QUEUED:
        return {
            'action': 'cancel',
            'block_reason': None,
            'will_undo': will_undo,
            'units_to_void': units_to_void,
        }

    return {
        'action': 'reverse',
        'block_reason': None,
        'will_undo': will_undo,
        'units_to_void': units_to_void,
    }


def build_entry_preview(entry_id: int, entry_dict_fn) -> dict:
    entry = _load_entry(entry_id)
    preview = _preview_action(entry)
    posting = entry_posting.get_posting(entry)
    label = entry_labels.get_label(entry)

    live_draws = []
    queued_draws = []
    for draw in _draws(entry):
        if _is_reversed(draw):
            continue
        draw_posting = entry_posting.get_posting(draw)
        if draw_posting and draw_posting.status == StockEntryPostingStatus.CANCELLED:
            continue
        detail = _linked_detail(draw, entry_dict_fn, step='draw')
        if draw_posting and draw_posting.status == StockEntryPostingStatus.QUEUED:
            queued_draws.append(detail)
        else:
            live_draws.append(detail)

    sibling = _transfer_sibling(entry)
    transfer_sibling = (
        _linked_detail(sibling, entry_dict_fn, step='transfer_sibling')
        if sibling is not None
        else None
    )

    reversal = None
    if _is_reversed(entry):
        try:
            rev = entry.reversed_by
            reversal = _linked_detail(rev, entry_dict_fn, step='reversal')
        except StockEntry.DoesNotExist:
            pass

    source_entry = None
    if entry.source_entry_id:
        src = (
            StockEntry.objects
            .select_related(*RELATED)
            .filter(pk=entry.source_entry_id)
            .first()
        )
        if src is not None:
            source_entry = _manage_entry_detail(src, entry_dict_fn)

    fifo = None
    try:
        override = entry.fifo_override
    except StockFifoOverride.DoesNotExist:
        override = None
    if override is not None:
        fifo = {
            'skipped': True,
            'reason': override.reason,
            'message': override.reason,
        }

    production = None
    consumptions = (
        StockEntry.objects
        .select_related(*RELATED)
        .filter(
            entry_type=StockEntryType.PRODUCTION_CONSUMPTION,
            source_entry_id=entry.pk,
        )
        .order_by('id')
    )
    if entry.entry_type == StockEntryType.PRODUCTION_OUTPUT:
        production = {
            'consumptions': [
                _linked_detail(c, entry_dict_fn, step='consumption')
                for c in consumptions
            ],
        }

    sticker_remaining = None
    if entry.entry_type == StockEntryType.RECEIPT:
        sticker_remaining = _dec(stickers.remaining_for_entry(entry))

    stock_units = [
        {
            'unit_serial': u.unit_serial,
            'status': u.status,
            'quantity_remaining': _dec(u.quantity_remaining),
        }
        for u in _stock_units(entry)
    ]

    label_info = None
    if label is not None:
        label_info = {
            'summary': f'{label.label_format} · {label.label_count} copies',
            'format': label.label_format,
            'copies': label.label_count,
            'verify_progress': label.status,
        }

    return {
        'entry_id': entry.id,
        'entry_code': _entry_code(entry.id),
        'entry_type': entry.entry_type,
        'is_reversed': _is_reversed(entry),
        'posting': (
            {
                'status': posting.status,
                'queued_at': (
                    posting.queued_at.isoformat() if posting.queued_at else None
                ),
            }
            if posting is not None
            else None
        ),
        'label': label_info,
        'sticker_remaining': sticker_remaining,
        'live_draws': live_draws,
        'queued_draws': queued_draws,
        'stock_units': stock_units,
        'source_entry': source_entry,
        'fifo_override': fifo,
        'transfer_sibling': transfer_sibling,
        'production': production,
        'reversal': reversal,
        'entry': _manage_entry_detail(entry, entry_dict_fn),
        'preview': preview,
    }


def _cancel_one(entry: StockEntry) -> str:
    posting = entry_posting.get_posting(entry)
    if posting is None:
        raise StockValidationError(
            f'{_entry_code(entry.id)} has no posting to cancel; reverse instead.',
        )
    if posting.status == StockEntryPostingStatus.POSTED:
        raise StockValidationError(
            f'{_entry_code(entry.id)} is posted; reverse instead.',
        )
    entry_posting.cancel_entry(entry_id=entry.id)
    return _entry_code(entry.id)


def _reverse_one(
    entry: StockEntry,
    *,
    idempotency_key: str,
    remarks: str,
    actor_user_id: int | None,
    lan_username: str | None,
    source_workstation: str | None,
    source_workstation_ip: str | None,
) -> str:
    if _is_reversed(entry):
        try:
            return _entry_code(entry.reversed_by.id)
        except StockEntry.DoesNotExist:
            return _entry_code(entry.id)
    services.reversal(
        idempotency_key=idempotency_key,
        entry=entry,
        actor_user_id=actor_user_id,
        lan_username=lan_username,
        source_workstation=source_workstation,
        source_workstation_ip=source_workstation_ip,
        remarks=remarks,
        authorised_by_user_id=actor_user_id,
    )
    return _entry_code(entry.id)


@transaction.atomic
def remove_entry(
    *,
    entry_id: int,
    reason: str,
    idempotency_key: str,
    entry_dict_fn,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
    source_workstation_ip: str | None = None,
) -> dict:
    reason = (reason or '').strip()
    if not reason:
        raise StockValidationError('reason is required.')
    if not (idempotency_key or '').strip():
        raise StockValidationError('idempotency_key is required.')

    entry = (
        StockEntry.objects
        .select_for_update(of=('self',))
        .select_related(*RELATED)
        .filter(pk=entry_id)
        .first()
    )
    if entry is None:
        raise StockValidationError(f'entry_id={entry_id} not found')

    preview_payload = build_entry_preview(entry_id, entry_dict_fn)
    action = preview_payload['preview']['action']

    if action == 'already_removed':
        return {
            'entry_id': entry.id,
            'entry_code': _entry_code(entry.id),
            'idempotency_key': idempotency_key,
            'cancelled_entry_codes': [],
            'reversed_entry_codes': [],
            'voided_unit_serials': [],
            'idempotent': True,
            'preview': preview_payload['preview'],
        }

    if action == 'blocked':
        raise StockValidationError(
            preview_payload['preview']['block_reason'] or 'Cannot remove this entry.',
        )

    cancelled: list[str] = []
    reversed_codes: list[str] = []
    voided: list[str] = []

    # Undo draws first (queued cancel, live reverse).
    for draw in _draws(entry):
        if _is_reversed(draw):
            continue
        draw_posting = entry_posting.get_posting(draw)
        if draw_posting and draw_posting.status == StockEntryPostingStatus.CANCELLED:
            continue
        if draw_posting and draw_posting.status == StockEntryPostingStatus.QUEUED:
            cancelled.append(_cancel_one(draw))
        else:
            reversed_codes.append(
                _reverse_one(
                    draw,
                    idempotency_key=f'{idempotency_key}:draw:{draw.id}',
                    remarks=reason,
                    actor_user_id=actor_user_id,
                    lan_username=lan_username,
                    source_workstation=source_workstation,
                    source_workstation_ip=source_workstation_ip,
                ),
            )

    sibling = _transfer_sibling(entry)
    targets = [entry]
    if sibling is not None and not _is_reversed(sibling):
        targets.append(sibling)

    if action == 'cancel':
        for target in targets:
            cancelled.append(_cancel_one(target))
    else:
        for target in targets:
            key = (
                idempotency_key
                if target.id == entry.id
                else f'{idempotency_key}:sibling:{target.id}'
            )
            reversed_codes.append(
                _reverse_one(
                    target,
                    idempotency_key=key,
                    remarks=reason,
                    actor_user_id=actor_user_id,
                    lan_username=lan_username,
                    source_workstation=source_workstation,
                    source_workstation_ip=source_workstation_ip,
                ),
            )

    for unit in _stock_units(entry):
        if unit.status in (StockUnitStatus.ACTIVE, StockUnitStatus.PARTIALLY_CONSUMED):
            void_unit(
                unit_serial=unit.unit_serial,
                reason=reason,
                actor_user_id=actor_user_id,
                lan_username=lan_username,
                source_workstation=source_workstation,
            )
            voided.append(unit.unit_serial)

    refreshed = build_entry_preview(entry_id, entry_dict_fn)
    return {
        'entry_id': entry.id,
        'entry_code': _entry_code(entry.id),
        'idempotency_key': idempotency_key,
        'cancelled_entry_codes': cancelled,
        'reversed_entry_codes': reversed_codes,
        'voided_unit_serials': voided,
        'idempotent': False,
        'preview': refreshed['preview'],
    }
