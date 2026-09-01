"""Stock Management Tool — preview remove (chunk 2); execute in later chunks."""

from __future__ import annotations

from decimal import Decimal

from stock_ledger.models import (
    StockEntry,
    StockEntryPostingStatus,
    StockEntryType,
    StockUnit,
    StockUnitStatus,
)
from stock_ledger.util import entry_labels, entry_posting
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
    posting = entry_posting.get_posting(entry)
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

    posting = entry_posting.get_posting(entry)
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
