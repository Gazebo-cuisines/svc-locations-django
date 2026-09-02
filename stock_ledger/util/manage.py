"""Stock Management Tool — preview + remove (queued cancel in chunk 3)."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from stock_ledger.models import (
    StockEntry,
    StockEntryPosting,
    StockEntryPostingStatus,
    StockEntryType,
    StockUnit,
    StockUnitStatus,
)
from stock_ledger.util import entry_labels, entry_posting, services, stickers, stock_units
from stock_ledger.util.activity import entry_activity_quantity
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.serialize import actor_names_for, manage_entry_detail
from stock_ledger.util.services import PRODUCTION_SOURCE_DOC, _entry_is_reversed

_TRACEABILITY_NOTE = (
    'Full history stays on the audit timeline. '
    'Old barcodes are void — do not reuse them.'
)

_ENTRY_TYPE_LABELS = {
    StockEntryType.RECEIPT: 'goods-in',
    StockEntryType.ISSUE: 'goods-out',
    StockEntryType.TRANSFER_OUT: 'transfer out',
    StockEntryType.TRANSFER_IN: 'transfer in',
    StockEntryType.PRODUCTION_OUTPUT: 'production output',
    StockEntryType.PRODUCTION_CONSUMPTION: 'production consume',
    StockEntryType.COUNT_ADJUSTMENT: 'count adjustment',
    StockEntryType.DISPOSAL: 'disposal',
    StockEntryType.REVERSAL: 'reversal',
    StockEntryType.DOWNTIME: 'downtime',
}

_ENTRY_SELECT = (
    'lot__product__unit',
    'lot__shape_format',
    'lot__product_supplier__outer_unit',
    'lot__product_supplier__inner_unit',
    'lot__product_supplier__purchase_shape_format',
    'unit',
    'location',
    'counterparty_location',
    'label',
    'posting',
    'source_entry__lot__product',
    'source_entry__unit',
    'source_entry__location',
    'reversed_by',
    'fifo_override__scanned_lot',
    'fifo_override__recommended_lot',
)


def _dec(value) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _undo_row(entry: StockEntry, step: str) -> dict:
    qty = entry_activity_quantity(entry)
    product = entry.lot.product if entry.lot_id else None
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'entry_type': entry.entry_type,
        'quantity': _dec(entry.quantity),
        'quantity_abs': qty.get('quantity_abs') or _dec(abs(entry.quantity)),
        'quantity_display': qty.get('quantity_display'),
        'product_id': product.id if product is not None else None,
        'product_name': product.name if product is not None else None,
        'source_entry_id': entry.source_entry_id,
        'source_entry_code': (
            entry_labels.entry_code(entry.source_entry_id)
            if entry.source_entry_id
            else None
        ),
        'step': step,
    }


def _qty_label(row: dict) -> str:
    return row.get('quantity_display') or row.get('quantity_abs') or row.get('quantity') or '?'


def _type_label(entry_type: str) -> str:
    return _ENTRY_TYPE_LABELS.get(entry_type, entry_type.replace('_', ' '))


def _build_confirmation_lines(
    *,
    action: str,
    will_undo: list[dict],
    units_to_void: list[dict],
    target: StockEntry,
) -> list[str]:
    if action in ('blocked', 'already_removed') or not will_undo:
        return []
    verb = 'Cancel' if action == 'cancel' else 'Reverse'
    lines: list[str] = []
    for i, row in enumerate(will_undo, start=1):
        label = _type_label(row['entry_type'])
        qty = _qty_label(row)
        code = row['entry_code']
        extra = ''
        if (
            row.get('source_entry_id') == target.id
            and row['entry_type'] != target.entry_type
        ):
            extra = ' (from this sticker)'
        lines.append(f'{i}. {verb} {label} {code} — {qty}{extra}')
    void_bits: list[str] = [entry_labels.entry_code(target.id)]
    serials = [u['unit_serial'] for u in units_to_void]
    if serials:
        shown = ', '.join(serials[:5])
        if len(serials) > 5:
            shown = f'{shown} (+{len(serials) - 5} more)'
        void_bits.append(f'unit labels {shown}')
    lines.append(
        f'{len(lines) + 1}. Void sticker {" and ".join(void_bits)}',
    )
    return lines


def _redo_todo_for_row(row: dict) -> dict | None:
    entry_type = row['entry_type']
    code = row['entry_code']
    qty = _qty_label(row)
    base = {
        'status': 'pending',
        'from_entry_code': code,
        'from_entry_type': entry_type,
        'quantity': row.get('quantity_abs') or row.get('quantity'),
        'quantity_display': row.get('quantity_display'),
        'product_id': row.get('product_id'),
        'product_name': row.get('product_name'),
    }
    if entry_type == StockEntryType.RECEIPT:
        return {
            **base,
            'id': f'redo_receipt_{code}',
            'kind': 'redo_goods_in',
            'title': 'Receive the correct quantity',
            'detail': (
                f'Goods-in (PO or adhoc): receive again with the correct qty '
                f'(was {qty}). New sticker will print.'
            ),
            'screen_hint': 'goods_in',
        }
    if entry_type == StockEntryType.ISSUE:
        return {
            **base,
            'id': f'redo_issue_{code}',
            'kind': 'redo_goods_out',
            'title': 'Re-issue the pick',
            'detail': (
                f'Goods-out (plan or adhoc): issue {qty} again from the '
                f'new goods-in sticker (if that pick was real).'
            ),
            'screen_hint': 'goods_out',
        }
    if entry_type == StockEntryType.TRANSFER_OUT:
        return {
            **base,
            'id': f'redo_transfer_{code}',
            'kind': 'redo_transfer',
            'title': 'Redo the transfer',
            'detail': (
                f'Goods-out transfer: move {qty} again between the same '
                f'locations. New stickers will print.'
            ),
            'screen_hint': 'goods_out',
        }
    if entry_type == StockEntryType.TRANSFER_IN:
        return None  # covered by transfer_out todo
    if entry_type == StockEntryType.PRODUCTION_OUTPUT:
        return {
            **base,
            'id': f'redo_production_{code}',
            'kind': 'redo_production',
            'title': 'Redo production if still needed',
            'detail': (
                f'Production: post MADE again for {qty} if that run was real.'
            ),
            'screen_hint': 'production',
        }
    if entry_type == StockEntryType.DISPOSAL:
        return {
            **base,
            'id': f'redo_disposal_{code}',
            'kind': 'redo_goods_out',
            'title': 'Redo the disposal',
            'detail': f'Disposal: record {qty} again if it was real.',
            'screen_hint': 'goods_out',
        }
    return None


def _build_redo_todos(
    *,
    action: str,
    will_undo: list[dict],
    units_to_void: list[dict],
    target: StockEntry,
) -> list[dict]:
    if action in ('blocked', 'already_removed'):
        return []
    todos: list[dict] = []
    # Outbound first (picks), then inbound — matches will_undo order for receipt+issue
    for row in will_undo:
        if row['entry_type'] in (
            StockEntryType.ISSUE,
            StockEntryType.TRANSFER_OUT,
            StockEntryType.DISPOSAL,
        ):
            todo = _redo_todo_for_row(row)
            if todo is not None:
                todos.append(todo)
    for row in will_undo:
        if row['entry_type'] in (
            StockEntryType.RECEIPT,
            StockEntryType.PRODUCTION_OUTPUT,
        ):
            todo = _redo_todo_for_row(row)
            if todo is not None:
                todos.append(todo)
    # Transfer_in alone (no out in list) — rare
    for row in will_undo:
        if row['entry_type'] == StockEntryType.TRANSFER_IN:
            if any(t['kind'] == 'redo_transfer' for t in todos):
                continue
            todos.append({
                'id': f'redo_transfer_in_{row["entry_code"]}',
                'kind': 'redo_transfer',
                'status': 'pending',
                'title': 'Redo the transfer',
                'detail': (
                    f'Transfer: move {_qty_label(row)} again. '
                    f'New stickers will print.'
                ),
                'from_entry_code': row['entry_code'],
                'from_entry_type': row['entry_type'],
                'quantity': row.get('quantity_abs') or row.get('quantity'),
                'quantity_display': row.get('quantity_display'),
                'product_id': row.get('product_id'),
                'product_name': row.get('product_name'),
                'screen_hint': 'goods_out',
            })

    sticker_code = entry_labels.entry_code(target.id)
    serials = [u['unit_serial'] for u in units_to_void]
    serial_bit = ''
    if serials:
        serial_bit = f' and unit labels {", ".join(serials[:5])}'
        if len(serials) > 5:
            serial_bit += f' (+{len(serials) - 5} more)'
    todos.append({
        'id': f'bin_stickers_{sticker_code}',
        'kind': 'bin_stickers',
        'status': 'pending',
        'title': 'Bin the old stickers',
        'detail': f'Bin void sticker {sticker_code}{serial_bit}. Do not reuse.',
        'from_entry_code': sticker_code,
        'from_entry_type': target.entry_type,
        'quantity': None,
        'quantity_display': None,
        'product_id': target.lot.product_id if target.lot_id else None,
        'product_name': (
            target.lot.product.name if target.lot_id else None
        ),
        'screen_hint': None,
    })
    return todos


def _enrich_operator_copy(
    preview: dict,
    *,
    target: StockEntry,
) -> dict:
    will_undo = preview.get('will_undo') or []
    units_to_void = preview.get('units_to_void') or []
    action = preview.get('action')
    preview['confirmation_lines'] = _build_confirmation_lines(
        action=action,
        will_undo=will_undo,
        units_to_void=units_to_void,
        target=target,
    )
    preview['traceability_note'] = (
        _TRACEABILITY_NOTE
        if action not in ('blocked',)
        else None
    )
    preview['redo_todos'] = _build_redo_todos(
        action=action,
        will_undo=will_undo,
        units_to_void=units_to_void,
        target=target,
    )
    return preview


def _draw_row(entry: StockEntry, *, actor_names: dict[int, str]) -> dict:
    return manage_entry_detail(entry, actor_names=actor_names)


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
        .select_related(*_ENTRY_SELECT)
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
        .select_related(*_ENTRY_SELECT)
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
        return _enrich_operator_copy(
            {
                'action': 'already_removed',
                'block_reason': None,
                'will_undo': [],
                'units_to_void': [],
            },
            target=entry,
        )

    posting = _get_posting(entry.id)
    if posting is not None and posting.status == StockEntryPostingStatus.QUEUED:
        legs = _transfer_legs(entry)
        preview = {
            'action': 'cancel',
            'block_reason': None,
            'will_undo': [_undo_row(leg, 'cancel') for leg in legs],
            'units_to_void': _units_to_void([leg.id for leg in legs]),
        }
        return _enrich_operator_copy(preview, target=entry)

    action, will_undo, block_reason = _plan_reverse(entry)
    entry_ids = {row['entry_id'] for row in will_undo}
    entry_ids.add(entry.id)
    sibling = transfer_sibling(entry)
    if sibling is not None:
        entry_ids.add(sibling.id)
    preview = {
        'action': action,
        'block_reason': block_reason,
        'will_undo': will_undo,
        'units_to_void': _units_to_void(list(entry_ids)),
    }
    return _enrich_operator_copy(preview, target=entry)


def _stock_unit_rows(entry_id: int) -> list[dict]:
    return [
        {
            'unit_serial': row.unit_serial,
            'status': row.status,
            'quantity_initial': _dec(row.quantity_initial),
            'quantity_remaining': _dec(row.quantity_remaining),
            'void_reason': row.void_reason,
            'voided_at': row.voided_at.isoformat() if row.voided_at else None,
        }
        for row in (
            StockUnit.objects
            .filter(created_by_entry_id=entry_id)
            .order_by('unit_serial')
        )
    ]


def _fifo_override_dict(entry: StockEntry) -> dict | None:
    try:
        row = entry.fifo_override
    except ObjectDoesNotExist:
        return None
    return {
        'reason': row.reason,
        'scanned_lot_id': row.scanned_lot_id,
        'scanned_trace_number': row.scanned_lot.trace_number,
        'recommended_lot_id': row.recommended_lot_id,
        'recommended_trace_number': row.recommended_lot.trace_number,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'actor_user_id': row.actor_user_id,
        'lan_username': row.lan_username,
    }


def _collect_actor_ids(
    entry: StockEntry,
    *,
    sibling: StockEntry | None,
    draws: list[StockEntry],
    source_entry: StockEntry | None,
) -> set[int]:
    ids: set[int] = set()
    for row in (entry, sibling, source_entry, *draws):
        if row is None:
            continue
        if row.actor_user_id is not None:
            ids.add(row.actor_user_id)
        if row.authorised_by_user_id is not None:
            ids.add(row.authorised_by_user_id)
        posting = entry_posting.get_posting(row)
        if posting is not None and posting.actor_user_id is not None:
            ids.add(posting.actor_user_id)
        label = entry_labels.get_label(row)
        if label is not None and label.actor_user_id is not None:
            ids.add(label.actor_user_id)
    try:
        reversal = entry.reversed_by
    except ObjectDoesNotExist:
        reversal = None
    if reversal is not None and reversal.actor_user_id is not None:
        ids.add(reversal.actor_user_id)
    return ids


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

    source_entry = entry.source_entry if entry.source_entry_id else None
    actor_ids = _collect_actor_ids(
        entry,
        sibling=sibling,
        draws=all_draws,
        source_entry=source_entry,
    )
    names = actor_names_for(actor_ids)

    label = entry_labels.get_label(entry)
    posting = entry_posting.get_posting(entry)
    entry_detail = manage_entry_detail(entry, actor_names=names)

    reversal = None
    try:
        reversal = entry.reversed_by
    except ObjectDoesNotExist:
        reversal = None

    production = None
    if entry.entry_type == StockEntryType.PRODUCTION_OUTPUT:
        consumptions = production_consumptions_for_output(entry)
        production = {
            'consumptions': [
                manage_entry_detail(cons, actor_names=names)
                for cons in consumptions
            ],
        }

    sticker_remaining = None
    if entry.entry_type == StockEntryType.RECEIPT:
        sticker_remaining = _dec(stickers.remaining_for_entry(entry))

    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'entry_type': entry.entry_type,
        'is_reversed': _entry_is_reversed(entry),
        'entry': entry_detail,
        'posting': entry_posting.posting_dict(posting) if posting else None,
        'label': entry_labels.label_state_dict(label) if label else None,
        'stock_units': _stock_unit_rows(entry.id),
        'sticker_remaining': sticker_remaining,
        'queued_draws': (
            stickers.queued_draws_for_entry(entry)
            if entry.entry_type == StockEntryType.RECEIPT
            else []
        ),
        'source_entry': (
            manage_entry_detail(source_entry, actor_names=names)
            if source_entry is not None
            else None
        ),
        'fifo_override': _fifo_override_dict(entry),
        'reversal': (
            manage_entry_detail(reversal, actor_names=names)
            if reversal is not None
            else None
        ),
        'live_draws': [_draw_row(d, actor_names=names) for d in all_draws],
        'production': production,
        'transfer_sibling': (
            {
                'entry_id': sibling.id,
                'entry_code': entry_labels.entry_code(sibling.id),
                'entry_type': sibling.entry_type,
                'entry': manage_entry_detail(sibling, actor_names=names),
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
    # Snapshot checklist before undo empties will_undo.
    confirmation_lines = list(preview.get('confirmation_lines') or [])
    redo_todos = list(preview.get('redo_todos') or [])
    traceability_note = preview.get('traceability_note')

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
    after_preview = preview_remove(entry)
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'idempotency_key': key,
        'cancelled_entry_codes': cancelled_codes,
        'reversed_entry_codes': reversed_codes,
        'voided_unit_serials': voided_serials,
        'preview': after_preview,
        'idempotent': action == 'already_removed',
        'confirmation_lines': confirmation_lines,
        'redo_todos': redo_todos,
        'traceability_note': traceability_note,
    }


def _list_manage_row(entry: StockEntry, *, actor_names: dict[int, str]) -> dict:
    from stock_ledger.util.activity import entry_activity_meta, entry_activity_quantity
    from stock_ledger.util.reports import movement_row

    base = movement_row(entry)
    qty = entry_activity_quantity(entry)
    meta = entry_activity_meta(entry)
    actor_name = None
    if entry.actor_user_id is not None:
        actor_name = actor_names.get(entry.actor_user_id)
    actor_name = actor_name or entry.lan_username
    return {
        'entry_id': entry.id,
        'entry_code': entry_labels.entry_code(entry.id),
        'entry_type': base['entry_type'],
        'recorded_at': base['recorded_at'],
        'effective_at': base['effective_at'],
        'product_id': base['product_id'],
        'product_name': base['product_name'],
        'recipe_code': base.get('recipe_code'),
        'trace_number': base.get('trace_number'),
        'use_by': qty.get('use_by') or base.get('use_by'),
        'location_id': base['location_id'],
        'location_name': base['location_name'],
        'quantity': base['quantity'],
        'quantity_display': qty.get('quantity_display'),
        'display_kg': qty.get('display_kg'),
        'pack_quantity': qty.get('pack_quantity'),
        'pack_unit_name': qty.get('pack_unit_name'),
        'shape_format_label': qty.get('shape_format_label'),
        'actor_user_id': entry.actor_user_id,
        'actor_name': actor_name,
        'lan_username': entry.lan_username,
        **meta,
    }


def list_manage_entries(
    *,
    product_id: int | None = None,
    location_id: int | None = None,
    entry_type: str | None = None,
    date_from=None,
    date_to=None,
    entry_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Manager grid: all entries, newest recorded_at first (includes queued)."""
    from django.db.models import Q

    qs = (
        StockEntry.objects
        .select_related(*_ENTRY_SELECT)
        .order_by('-recorded_at', '-id')
    )
    if product_id is not None:
        qs = qs.filter(lot__product_id=product_id)
    if location_id is not None:
        qs = qs.filter(
            Q(location_id=location_id) | Q(counterparty_location_id=location_id),
        )
    if entry_type not in (None, ''):
        qs = qs.filter(entry_type=entry_type)
    if date_from is not None:
        qs = qs.filter(recorded_at__date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(recorded_at__date__lte=date_to)
    if entry_id is not None:
        qs = qs.filter(pk=entry_id)

    count = qs.count()
    page = list(qs[offset : offset + limit])
    actor_ids = {e.actor_user_id for e in page if e.actor_user_id is not None}
    names = actor_names_for(actor_ids)
    items = [_list_manage_row(entry, actor_names=names) for entry in page]
    return {
        'items': items,
        'count': count,
        'limit': limit,
        'offset': offset,
        'has_more': offset + len(items) < count,
        'order': 'recorded_at_desc',
    }
