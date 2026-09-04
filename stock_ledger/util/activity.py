"""Activity-feed flags for stock entries (my day / operator views)."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist

from stock_ledger.models import (
    StockEntry,
    StockEntryPostingStatus,
    StockEntryType,
)
from stock_ledger.util import entry_labels, entry_posting
from stock_ledger.util.product_supplier_lookup import product_supplier_for_entry
from stock_ledger.util.serialize import _display_grams, supplier_pack_fields
from users_rbac.models import RbacUser

_MANAGER_PREFIX = 'Manager remove:'


def _dec(value) -> str | None:
    if value is None:
        return None
    text = format(Decimal(str(value)), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def entry_activity_quantity(entry: StockEntry) -> dict:
    """Pack shape + kg/grams display for activity rows (not raw ledger unit only)."""
    lot = entry.lot
    product = lot.product if lot is not None else None
    mapping = product_supplier_for_entry(entry)
    stock_qty = abs(entry.quantity) if entry.quantity is not None else Decimal('0')
    pack = supplier_pack_fields(stock_qty, product, mapping)
    entry_unit_name = entry.unit.name if entry.unit_id else None
    display_kg = pack.get('display_kg')
    if (
        display_kg is None
        and product is not None
        and product.unit_id
        and (product.unit.name or '').lower() == 'kg'
    ):
        display_kg = _dec(stock_qty)

    fields = {
        'unit_name': product.unit.name if product is not None and product.unit_id else None,
        'entry_unit_name': entry_unit_name,
        'use_by': lot.use_by.isoformat() if lot is not None and lot.use_by else None,
        'quantity_base': _dec(entry.quantity_base),
        'display_kg': display_kg,
        'display_grams': _display_grams(
            stock_qty,
            entry_unit_name,
            display_kg,
        ),
        'pack_quantity': _dec(pack.get('pack_quantity')) if pack.get('pack_quantity') else None,
        'pack_unit_name': pack.get('pack_unit_name'),
        'shape_format_label': pack.get('shape_format_label'),
        'quantity_display': None,
    }

    if mapping is not None:
        fields['shape_outer_qty'] = _dec(mapping.outer_qty)
        fields['shape_outer_unit_name'] = (
            mapping.outer_unit.name if mapping.outer_unit_id else None
        )
        fields['shape_inner_qty'] = _dec(mapping.inner_qty)
        fields['shape_inner_unit_name'] = (
            mapping.inner_unit.name if mapping.inner_unit_id else None
        )
        fields['shape_multiplier'] = _dec(mapping.multiplier)

    pack_qty = fields['pack_quantity']
    pack_unit = fields['pack_unit_name']
    shape_label = fields['shape_format_label']
    if pack_qty and pack_unit and shape_label:
        fields['quantity_display'] = f'{pack_qty} {pack_unit} ({shape_label} each)'
    elif pack_qty and pack_unit and display_kg:
        fields['quantity_display'] = f'{pack_qty} {pack_unit} = {display_kg} kg'
    elif display_kg:
        fields['quantity_display'] = f'{display_kg} kg'
    elif entry.quantity is not None:
        unit = fields['unit_name'] or entry_unit_name or ''
        fields['quantity_display'] = f'{_dec(stock_qty)} {unit}'.strip()

    return fields


def _manager_meta(posting) -> dict | None:
    if posting is None:
        return None
    meta = posting.meta or {}
    mgr = meta.get('manager_remove')
    return mgr if isinstance(mgr, dict) else None


def entry_activity_flags(entry: StockEntry) -> dict:
    """
    Live vs removed for activity UIs.

    is_live=False when cancelled (unposted) or reversed (posted).
    manager_removed=True when Stock Management Tool did the remove.
    user_cancelled=True when the operator cancelled a queued posting.
    """
    flags = {
        'entry_code': entry_labels.entry_code(entry.id),
        'is_live': True,
        'is_removed': False,
        'manager_removed': False,
        'user_cancelled': False,
        'remove_reason': None,
        'removed_at': None,
        'removed_by_user_id': None,
        'removed_by_name': None,
    }

    posting = entry_posting.get_posting(entry)
    if (
        posting is not None
        and posting.status == StockEntryPostingStatus.CANCELLED
    ):
        flags['is_live'] = False
        flags['is_removed'] = True
        flags['removed_at'] = (
            posting.cancelled_at.isoformat() if posting.cancelled_at else None
        )
        mgr = _manager_meta(posting)
        if mgr:
            flags['manager_removed'] = True
            flags['remove_reason'] = mgr.get('reason')
            flags['removed_at'] = mgr.get('removed_at') or flags['removed_at']
            flags['removed_by_user_id'] = mgr.get('actor_user_id')
            flags['removed_by_name'] = mgr.get('lan_username')
        else:
            flags['user_cancelled'] = True
            flags['removed_by_user_id'] = posting.actor_user_id or entry.actor_user_id
            flags['removed_by_name'] = posting.lan_username or entry.lan_username
        return flags

    reversal = None
    try:
        reversal = entry.reversed_by
    except ObjectDoesNotExist:
        reversal = None

    if reversal is not None:
        flags['is_live'] = False
        flags['is_removed'] = True
        remarks = (reversal.remarks or '').strip()
        if remarks.startswith(_MANAGER_PREFIX):
            flags['manager_removed'] = True
            flags['remove_reason'] = remarks[len(_MANAGER_PREFIX):].strip()
        elif reversal.override_reason:
            flags['remove_reason'] = reversal.override_reason
        flags['removed_at'] = (
            reversal.recorded_at.isoformat() if reversal.recorded_at else None
        )
        flags['removed_by_user_id'] = reversal.actor_user_id
        flags['removed_by_name'] = reversal.lan_username

    return flags


_LABEL_ENTRY_TYPES = frozenset({
    StockEntryType.RECEIPT,
    StockEntryType.TRANSFER_OUT,
})


def entry_activity_status(entry: StockEntry, *, flags: dict | None = None) -> dict:
    """Posting/label state for activity icons + reprint affordances."""
    flags = flags if flags is not None else entry_activity_flags(entry)
    posting = entry_posting.get_posting(entry)
    label = entry_labels.get_label(entry)

    posting_status = posting.status if posting is not None else None
    label_status = label.status if label is not None else None

    if flags['manager_removed']:
        ui_status = 'removed'
    elif flags['user_cancelled'] or posting_status == StockEntryPostingStatus.CANCELLED:
        ui_status = 'cancelled'
    elif flags['is_removed']:
        ui_status = 'removed'
    elif posting_status == StockEntryPostingStatus.QUEUED:
        ui_status = 'queued'
    elif posting_status == StockEntryPostingStatus.POSTED:
        ui_status = 'posted'
    else:
        ui_status = 'posted'

    is_live = flags['is_live']
    has_label = label is not None
    can_reprint_label = (
        is_live
        and has_label
        and entry.entry_type in _LABEL_ENTRY_TYPES
    )

    out = {
        'posting_status': posting_status,
        'label_status': label_status,
        'ui_status': ui_status,
        'can_reprint_label': can_reprint_label,
    }
    if can_reprint_label:
        out['label_reprint_path'] = f'/stock/entries/{entry.id}/labels/print/'
    if has_label:
        out['label_format'] = label.label_format
        out['label_count'] = label.label_count
    return out


def entry_activity_meta(entry: StockEntry) -> dict:
    flags = entry_activity_flags(entry)
    return {**flags, **entry_activity_status(entry, flags=flags)}


def enrich_activity_names(items: list[dict]) -> list[dict]:
    """Resolve removed_by_name from user id when only lan_username is stored."""
    ids = {
        row['removed_by_user_id']
        for row in items
        if row.get('removed_by_user_id') and not row.get('removed_by_name')
    }
    if not ids:
        return items
    names = {
        user.id: (user.display_name or user.username)
        for user in RbacUser.objects.filter(pk__in=ids).only(
            'id', 'display_name', 'username',
        )
    }
    for row in items:
        uid = row.get('removed_by_user_id')
        if uid and not row.get('removed_by_name'):
            row['removed_by_name'] = names.get(uid)
    return items
