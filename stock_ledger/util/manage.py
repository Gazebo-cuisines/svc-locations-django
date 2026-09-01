"""Stock Management Tool — preview + remove (queued cancel in chunk 3)."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from stock_ledger.models import (
    StockEntry,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
    StockUnit,
    StockUnitStatus,
)
from stock_ledger.util import entry_labels, entry_posting, services, stock_units
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.services import PRODUCTION_SOURCE_DOC, _entry_is_reversed

_ENTRY_SELECT = (
    'lot__product',
    'lot__shape_format',
    'unit',
    'location',
    'counterparty_location',
    'label',
    'posting',
)


def _dec(value) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _undo_row(entry: StockEntry, step: str) -> dict:
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'entry_type': entry.entry_type,
        'quantity': _dec(entry.quantity),
        'step': step,
    }


def _draw_row(entry: StockEntry) -> dict:
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'entry_type': entry.entry_type,
        'quantity': _dec(entry.quantity),
        'location_id': entry.location_id,
    }


def get_entry_for_manage(entry_id: int) -> StockEntry:
    return (
        StockEntry.objects
        .select_related(*_ENTRY_SELECT)
        .get(pk=entry_id)
    )


def live_draws(entry: StockEntry) -> list[StockEntry]:
    return list(
        StockEntry.objects
        .filter(source_entry_id=entry.pk, reversed_by__isnull=True)
        .exclude(posting__status=StockEntryPostingStatus.CANCELLED)
        .select_related('lot__product', 'unit', 'location')
        .order_by('id')
    )


def transfer_sibling(entry: StockEntry) -> StockEntry | None:
    if not entry.transfer_group_id:
        return None
    return (
        StockEntry.objects
        .filter(transfer_group_id=entry.transfer_group_id)
        .exclude(pk=entry.pk)
        .select_related(*_ENTRY_SELECT)
        .first()
    )


def production_consumptions_for_output(output: StockEntry) -> list[StockEntry]:
    return list(
        StockEntry.objects
        .filter(
            entry_type=StockEntryType.PRODUCTION_CONSUMPTION,
            source_document_type=PRODUCTION_SOURCE_DOC,
            source_document_id=output.id,
        )
        .select_related('lot__product', 'unit', 'location')
        .order_by('id')
    )


def _is_already_removed(entry: StockEntry) -> bool:
    if entry.entry_type == StockEntryType.REVERSAL:
        return True
    if _entry_is_reversed(entry):
        return True
    posting = _get_posting(entry.id)
    return (
        posting is not None
        and posting.status == StockEntryPostingStatus.CANCELLED
    )


def _transfer_legs(entry: StockEntry) -> list[StockEntry]:
    if entry.entry_type not in (
        StockEntryType.TRANSFER_OUT,
        StockEntryType.TRANSFER_IN,
    ):
        return [entry]
    sibling = transfer_sibling(entry)
    if sibling is None or _entry_is_reversed(sibling):
        return [entry]
    out = entry if entry.entry_type == StockEntryType.TRANSFER_OUT else sibling
    inn = sibling if entry is out else entry
    return [out, inn]


def _units_to_void(entry_ids: list[int]) -> list[dict]:
    if not entry_ids:
        return []
    return [
        {'unit_serial': row.unit_serial, 'status': row.status}
        for row in (
            StockUnit.objects
            .filter(created_by_entry_id__in=entry_ids)
            .exclude(status=StockUnitStatus.VOID)
            .order_by('unit_serial')
        )
    ]


def _plan_reverse(entry: StockEntry) -> tuple[str, list[dict], str | None]:
    """Return action, will_undo rows, block_reason."""
    if entry.entry_type == StockEntryType.PRODUCTION_CONSUMPTION:
        output_id = entry.source_document_id
        code = entry_labels.entry_code(output_id) if output_id else '?'
        return (
            'blocked',
            [],
            f'This stock was used in production. Void {code} first, then retry.',
        )

    if entry.entry_type == StockEntryType.PRODUCTION_OUTPUT:
        will_undo: list[dict] = []
        for cons in production_consumptions_for_output(entry):
            if not _entry_is_reversed(cons):
                will_undo.append(_undo_row(cons, 'reverse'))
        if not _entry_is_reversed(entry):
            will_undo.append(_undo_row(entry, 'reverse'))
        return 'reverse', will_undo, None

    will_undo: list[dict] = []
    legs = _transfer_legs(entry)
    for leg in legs:
        draws = live_draws(leg)
        prod_draws = [
            d for d in draws
            if d.entry_type == StockEntryType.PRODUCTION_CONSUMPTION
        ]
        if prod_draws:
            output_id = prod_draws[0].source_document_id
            code = (
                entry_labels.entry_code(output_id)
                if output_id
                else '?'
            )
            return (
                'blocked',
                [],
                (
                    f'This stock was used in production ({code}). '
                    'Void that production run first, then retry.'
                ),
            )
        for draw in draws:
            will_undo.append(_undo_row(draw, 'reverse'))
        if not _entry_is_reversed(leg):
            will_undo.append(_undo_row(leg, 'reverse'))

    return 'reverse', will_undo, None


def preview_remove(entry: StockEntry) -> dict:
    if _is_already_removed(entry):
        return {
            'action': 'already_removed',
            'block_reason': None,
            'will_undo': [],
            'units_to_void': [],
        }

    posting = _get_posting(entry.id)
    if posting is not None and posting.status == StockEntryPostingStatus.QUEUED:
        legs = _transfer_legs(entry)
        return {
            'action': 'cancel',
            'block_reason': None,
            'will_undo': [_undo_row(leg, 'cancel') for leg in legs],
            'units_to_void': _units_to_void([leg.id for leg in legs]),
        }

    action, will_undo, block_reason = _plan_reverse(entry)
    entry_ids = {row['entry_id'] for row in will_undo}
    entry_ids.add(entry.id)
    sibling = transfer_sibling(entry)
    if sibling is not None:
        entry_ids.add(sibling.id)
    return {
        'action': action,
        'block_reason': block_reason,
        'will_undo': will_undo,
        'units_to_void': _units_to_void(list(entry_ids)),
    }


def build_manage_detail(entry: StockEntry) -> dict:
    sibling = transfer_sibling(entry)
    legs = _transfer_legs(entry)
    all_draws: list[StockEntry] = []
    seen_draw_ids: set[int] = set()
    for leg in legs:
        for draw in live_draws(leg):
            if draw.id not in seen_draw_ids:
                seen_draw_ids.add(draw.id)
                all_draws.append(draw)

    label = entry_labels.get_label(entry)
    posting = entry_posting.get_posting(entry)

    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'entry_type': entry.entry_type,
        'is_reversed': _entry_is_reversed(entry),
        'posting': entry_posting.posting_dict(posting) if posting else None,
        'label': entry_labels.label_state_dict(label) if label else None,
        'live_draws': [_draw_row(d) for d in all_draws],
        'transfer_sibling': (
            {
                'entry_id': sibling.id,
                'entry_code': entry_labels.entry_code(sibling.id),
                'entry_type': sibling.entry_type,
            }
            if sibling is not None
            else None
        ),
        'preview': preview_remove(entry),
    }


def _is_reverse_preview_executable(preview: dict, entry: StockEntry) -> bool:
    if preview['action'] != 'reverse':
        return False
    if entry.entry_type == StockEntryType.PRODUCTION_OUTPUT:
        return False
    will_undo = preview.get('will_undo') or []
    if not will_undo:
        return False
    return all(row.get('step') == 'reverse' for row in will_undo)


def _is_production_void_executable(preview: dict, entry: StockEntry) -> bool:
    if preview['action'] != 'reverse':
        return False
    if entry.entry_type != StockEntryType.PRODUCTION_OUTPUT:
        return False
    will_undo = preview.get('will_undo') or []
    if not will_undo:
        return False
    return all(row.get('step') == 'reverse' for row in will_undo)


def _production_void_codes(preview: dict) -> tuple[list[str], list[int]]:
    reversed_codes: list[str] = []
    entry_ids: list[int] = []
    for row in preview.get('will_undo') or []:
        if row.get('step') != 'reverse':
            continue
        reversed_codes.append(row['entry_code'])
        entry_ids.append(row['entry_id'])
    return reversed_codes, entry_ids


def _production_void_idempotency_key(manager_key: str, entry_id: int) -> str:
    """Hash so production_void's :consume:{id} suffixes stay within varchar(64)."""
    return hashlib.sha256(
        f'{manager_key}:production_void:{entry_id}'.encode(),
    ).hexdigest()[:44]


def _execute_production_void(
    entry: StockEntry,
    preview: dict,
    *,
    reason: str,
    idempotency_key: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> tuple[list[str], list[int]]:
    void_kwargs = {
        'remarks': f'Manager remove: {reason[:400]}',
        'actor_user_id': actor_user_id,
        'lan_username': lan_username,
        'source_workstation': source_workstation,
    }
    if not _entry_is_reversed(entry):
        try:
            services.production_void(
                entry_id=entry.id,
                idempotency_key=_production_void_idempotency_key(
                    idempotency_key,
                    entry.id,
                ),
                **void_kwargs,
            )
        except StockValidationError as exc:
            if 'already reversed' not in str(exc).lower():
                raise ManageRemoveError(str(exc), status_code=409) from exc
    return _production_void_codes(preview)


def _execute_reverse_plan(
    preview: dict,
    *,
    reason: str,
    idempotency_key: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> tuple[list[str], list[int]]:
    reversed_codes: list[str] = []
    entry_ids: list[int] = []
    for row in preview.get('will_undo') or []:
        if row.get('step') != 'reverse':
            continue
        target_id = row['entry_id']
        target = StockEntry.objects.get(pk=target_id)
        if not _entry_is_reversed(target):
            _reversal_for_leg(
                target,
                reason=reason,
                idempotency_key=idempotency_key,
                actor_user_id=actor_user_id,
                lan_username=lan_username,
                source_workstation=source_workstation,
            )
        reversed_codes.append(entry_labels.entry_code(target_id))
        entry_ids.append(target_id)
    return reversed_codes, entry_ids


def _reversal_for_leg(
    leg: StockEntry,
    *,
    reason: str,
    idempotency_key: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> StockEntry:
    return services.reversal(
        idempotency_key=f'{idempotency_key}:reverse:{leg.id}',
        entry=leg,
        remarks=f'Manager remove: {reason[:400]}',
        actor_user_id=actor_user_id,
        lan_username=lan_username,
        source_workstation=source_workstation,
    )


def _get_posting(entry_id: int) -> StockEntryPosting | None:
    return StockEntryPosting.objects.filter(stock_entry_id=entry_id).first()


class ManageRemoveError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _void_units_for_entries(
    entry_ids: list[int],
    *,
    reason: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> list[str]:
    voided: list[str] = []
    for unit in (
        StockUnit.objects
        .filter(created_by_entry_id__in=entry_ids)
        .exclude(status=StockUnitStatus.VOID)
        .order_by('unit_serial')
    ):
        stock_units.void_unit(
            unit_serial=unit.unit_serial,
            reason=reason,
            actor_user_id=actor_user_id,
            lan_username=lan_username,
            source_workstation=source_workstation,
        )
        voided.append(unit.unit_serial)
    return voided


def _cancel_queued_leg(
    leg: StockEntry,
    *,
    reason: str,
    idempotency_key: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> bool:
    """Cancel a queued posting. Returns False if leg has no posting row."""
    posting = _get_posting(leg.id)
    if posting is None:
        return False
    if posting.status == StockEntryPostingStatus.CANCELLED:
        return True
    if posting.status != StockEntryPostingStatus.QUEUED:
        raise ManageRemoveError(
            f'{entry_labels.entry_code(leg.id)} posting is {posting.status}.',
            status_code=409,
        )
    entry_posting.cancel_entry(entry_id=leg.id)
    posting = _get_posting(leg.id)
    if posting is None:
        return True
    meta = dict(posting.meta or {})
    meta['manager_remove'] = {
        'idempotency_key': idempotency_key,
        'reason': reason[:500],
        'actor_user_id': actor_user_id,
        'lan_username': lan_username,
        'removed_at': timezone.now().isoformat(),
    }
    posting.meta = meta
    if actor_user_id is not None:
        posting.actor_user_id = actor_user_id
    if lan_username is not None:
        posting.lan_username = lan_username
    if source_workstation is not None:
        posting.source_workstation = source_workstation
    posting.save(
        update_fields=[
            'meta',
            'actor_user_id',
            'lan_username',
            'source_workstation',
        ],
    )
    return True


@transaction.atomic
def remove_entry(
    *,
    entry_id: int,
    reason: str,
    idempotency_key: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> dict:
    reason = (reason or '').strip()
    if not reason:
        raise ManageRemoveError('reason is required.')
    key = (idempotency_key or '').strip()
    if not key:
        raise ManageRemoveError('idempotency_key is required.')

    entry = get_entry_for_manage(entry_id)
    preview = preview_remove(entry)
    action = preview['action']

    if action == 'blocked':
        raise ManageRemoveError(
            preview['block_reason'] or 'Remove is blocked.',
            status_code=409,
        )

    cancelled_codes: list[str] = []
    reversed_codes: list[str] = []
    voided_serials: list[str] = []

    if action == 'reverse':
        if entry.entry_type == StockEntryType.PRODUCTION_OUTPUT:
            if not _is_production_void_executable(preview, entry):
                raise ManageRemoveError(
                    'This production remove is not available yet.',
                    status_code=409,
                )
            reversed_codes, void_entry_ids = _execute_production_void(
                entry,
                preview,
                reason=reason,
                idempotency_key=key,
                actor_user_id=actor_user_id,
                lan_username=lan_username,
                source_workstation=source_workstation,
            )
        elif _is_reverse_preview_executable(preview, entry):
            reversed_codes, void_entry_ids = _execute_reverse_plan(
                preview,
                reason=reason,
                idempotency_key=key,
                actor_user_id=actor_user_id,
                lan_username=lan_username,
                source_workstation=source_workstation,
            )
        else:
            raise ManageRemoveError(
                'This remove is not available yet.',
                status_code=409,
            )
        voided_serials = _void_units_for_entries(
            void_entry_ids,
            reason=reason,
            actor_user_id=actor_user_id,
            lan_username=lan_username,
            source_workstation=source_workstation,
        )
    elif action == 'cancel':
        legs = _transfer_legs(entry)
        leg_ids = [leg.id for leg in legs]

        for leg in legs:
            if not _cancel_queued_leg(
                leg,
                reason=reason,
                idempotency_key=key,
                actor_user_id=actor_user_id,
                lan_username=lan_username,
                source_workstation=source_workstation,
            ):
                continue
            posting = _get_posting(leg.id)
            if (
                posting is not None
                and posting.status == StockEntryPostingStatus.CANCELLED
            ):
                cancelled_codes.append(entry_labels.entry_code(leg.id))

        if not cancelled_codes:
            raise ManageRemoveError(
                'This entry has no queued posting to cancel.',
                status_code=409,
            )

        voided_serials = _void_units_for_entries(
            leg_ids,
            reason=reason,
            actor_user_id=actor_user_id,
            lan_username=lan_username,
            source_workstation=source_workstation,
        )
    elif action != 'already_removed':
        raise ManageRemoveError(
            f'Cannot remove entry (action={action}).',
            status_code=409,
        )

    entry = get_entry_for_manage(entry_id)
    preview = preview_remove(entry)
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'idempotency_key': key,
        'cancelled_entry_codes': cancelled_codes,
        'reversed_entry_codes': reversed_codes,
        'voided_unit_serials': voided_serials,
        'preview': preview,
        'idempotent': action == 'already_removed',
    }
