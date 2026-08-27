"""Physical stock unit labels — thin layer on top of the ledger."""

from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from product.models import ProductLabelMode
from stock_ledger.models import (
    StockEntry,
    StockEntryType,
    StockUnit,
    StockUnitConsumption,
    StockUnitPrintEvent,
    StockUnitPrintReason,
    StockUnitStatus,
)
from stock_ledger.util import services
from stock_ledger.util.conversions import StockValidationError

_CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
_PRINTABLE_ENTRY_TYPES = frozenset({
    StockEntryType.RECEIPT,
    StockEntryType.PRODUCTION_OUTPUT,
})


def generate_unit_serial(*, seed: str | None = None) -> str:
    raw = (
        hashlib.sha256(seed.encode('utf-8')).digest()
        if seed
        else secrets.token_bytes(16)
    )
    n = int.from_bytes(raw[:8], 'big')
    chars = []
    for _ in range(12):
        chars.append(_CROCKFORD[n % 32])
        n //= 32
    return ''.join(reversed(chars))


def resolve_unit_serial(raw: str) -> str:
    """Accept bare serial or full GS1 string containing AI(21)."""
    text = (raw or '').strip()
    if not text:
        raise StockValidationError('unit_serial is required')
    marker = '(21)'
    if marker in text:
        rest = text.split(marker, 1)[1]
        # next AI starts with '(' 
        end = rest.find('(')
        text = rest if end < 0 else rest[:end]
    return text.strip()


def get_unit_by_serial(unit_serial: str) -> StockUnit:
    serial = resolve_unit_serial(unit_serial)
    unit = (
        StockUnit.objects
        .select_related('lot__product', 'location', 'unit', 'created_by_entry')
        .filter(unit_serial=serial)
        .first()
    )
    if unit is None:
        raise StockValidationError(f'unit_serial={serial} not found')
    return unit


def build_gs1_payload(unit: StockUnit) -> dict:
    """Ready for bwip-js bcid=gs1datamatrix."""
    lot = unit.lot
    product = lot.product
    gtin = (product.external_barcode or '').strip() or None
    if gtin and gtin.isdigit() and len(gtin) < 14:
        gtin = gtin.zfill(14)

    batch = lot.trace_number or ''
    expiry = lot.use_by.strftime('%y%m%d') if lot.use_by else None
    serial = unit.unit_serial

    parts = []
    if gtin:
        parts.append(f'(01){gtin}')
    if batch:
        parts.append(f'(10){batch}')
    if expiry:
        parts.append(f'(17){expiry}')
    parts.append(f'(21){serial}')
    payload_string = ''.join(parts)

    return {
        'gtin': gtin,
        'batch': batch or None,
        'expiry': expiry,
        'serial': serial,
        'payload_string': payload_string,
        'human_readable': {
            'product_name': product.name,
            'quantity': str(unit.quantity_initial),
            'quantity_remaining': str(unit.quantity_remaining),
            'use_by': lot.use_by.isoformat() if lot.use_by else None,
            'production_date': (
                lot.production_date.isoformat() if lot.production_date else None
            ),
            'trace_number': lot.trace_number,
            'unit_serial': serial,
        },
    }


def product_code(product) -> str:
    """The only identifier a product label carries."""
    return f'P{product.id}'


def build_product_label(*, product, lot=None) -> dict:
    """
    Reusable product label. Barcode holds our product id and nothing else, so a
    damaged label can still be typed in by hand. Batch text comes from the lot.
    """
    code = product_code(product)
    return {
        'bcid': 'datamatrix',
        'payload_string': code,
        'product_code': code,
        'human_readable': {
            'product_id': product.id,
            'product_code': code,
            'product_name': product.name,
            'recipe_code': product.recipe_code,
            'unit_name': product.unit.name if product.unit_id else None,
            'label_mode': product.label_mode,
            'lot_id': lot.id if lot is not None else None,
            'trace_number': lot.trace_number if lot is not None else None,
            'use_by': (
                lot.use_by.isoformat() if lot is not None and lot.use_by else None
            ),
            'production_date': (
                lot.production_date.isoformat()
                if lot is not None and lot.production_date
                else None
            ),
        },
    }


def _check_label_mode(
    *,
    product,
    unit_count: int,
    quantity_per_unit: Decimal,
    entry_qty: Decimal,
) -> None:
    """Staff cannot sticker 50 frozen bags or 350 trays, so the product decides."""
    if product.label_mode == ProductLabelMode.PRODUCT:
        raise StockValidationError(
            f'{product.name} uses a reusable product label. Print it from '
            f'/stock/products/{product.id}/label/ instead of batch labels.'
        )
    if product.label_mode == ProductLabelMode.BATCH:
        if unit_count != 1:
            raise StockValidationError(
                f'{product.name} takes one label per batch, so unit_count must '
                f'be 1 (got {unit_count}).'
            )
        if quantity_per_unit != entry_qty:
            raise StockValidationError(
                f'{product.name} takes one label for the whole batch, so '
                f'quantity_per_unit must be {entry_qty} (got {quantity_per_unit}).'
            )


def create_units_for_entry(
    *,
    source_entry: StockEntry,
    unit_count: int,
    quantity_per_unit: Decimal,
    idempotency_key_prefix: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> list[StockUnit]:
    if unit_count < 1:
        raise StockValidationError('unit_count must be >= 1')
    if quantity_per_unit <= 0:
        raise StockValidationError('quantity_per_unit must be positive')
    if source_entry.entry_type not in _PRINTABLE_ENTRY_TYPES:
        raise StockValidationError(
            f'can only print against receipt or production_output '
            f'(got {source_entry.entry_type})'
        )

    entry_qty = abs(source_entry.quantity)
    new_total = quantity_per_unit * unit_count
    _check_label_mode(
        product=source_entry.lot.product,
        unit_count=unit_count,
        quantity_per_unit=quantity_per_unit,
        entry_qty=entry_qty,
    )

    with transaction.atomic():
        already = (
            StockUnit.objects
            .select_for_update()
            .filter(created_by_entry_id=source_entry.id)
            .exclude(status=StockUnitStatus.VOID)
            .aggregate(s=Sum('quantity_initial'))
            ['s']
            or Decimal('0')
        )

        # Idempotent replay: same prefix already created these serials.
        serials = [
            generate_unit_serial(
                seed=f'{idempotency_key_prefix}:{source_entry.id}:{i}',
            )
            for i in range(unit_count)
        ]
        existing = list(
            StockUnit.objects
            .filter(unit_serial__in=serials)
            .select_related('lot__product', 'unit', 'location')
            .order_by('id')
        )
        if len(existing) == unit_count:
            return existing
        if existing:
            raise StockValidationError(
                'partial print exists for this idempotency_key_prefix; '
                'retry with a new prefix or wait'
            )

        if already + new_total > entry_qty:
            raise StockValidationError(
                f'over-print: {already} already labeled + {new_total} requested '
                f'> entry quantity {entry_qty}'
            )

        now = timezone.now()
        created: list[StockUnit] = []
        for serial in serials:
            unit = StockUnit.objects.create(
                unit_serial=serial,
                lot_id=source_entry.lot_id,
                location_id=source_entry.location_id,
                unit_id=source_entry.unit_id,
                quantity_initial=quantity_per_unit,
                quantity_remaining=quantity_per_unit,
                status=StockUnitStatus.ACTIVE,
                created_by_entry_id=source_entry.id,
            )
            StockUnitPrintEvent.objects.create(
                stock_unit=unit,
                printed_at=now,
                actor_user_id=actor_user_id,
                lan_username=lan_username,
                source_workstation=source_workstation,
                reason=StockUnitPrintReason.INITIAL,
            )
            created.append(unit)

        return list(
            StockUnit.objects
            .filter(pk__in=[u.pk for u in created])
            .select_related('lot__product', 'unit', 'location')
            .order_by('id')
        )


_CONSUMABLE_STATUSES = frozenset({
    StockUnitStatus.ACTIVE,
    StockUnitStatus.PARTIALLY_CONSUMED,
})

_CONSUME_ENTRY_TYPES = frozenset({
    StockEntryType.ISSUE,
    StockEntryType.DISPOSAL,
    StockEntryType.PRODUCTION_CONSUMPTION,
})


def consume_unit(
    *,
    unit_serial: str,
    entry_type: str,
    quantity: Decimal,
    idempotency_key: str,
    output_entry_id: int | None = None,
    effective_at=None,
    **audit_kwargs,
) -> dict:
    """Post ledger consume/issue/disposal, then draw down the physical unit."""
    if quantity <= 0:
        raise StockValidationError('consume quantity must be positive')
    if entry_type not in _CONSUME_ENTRY_TYPES:
        raise StockValidationError(
            f'entry_type must be one of: {", ".join(sorted(_CONSUME_ENTRY_TYPES))}'
        )
    if (
        entry_type == StockEntryType.PRODUCTION_CONSUMPTION
        and output_entry_id is None
    ):
        raise StockValidationError(
            'output_entry_id is required for production_consumption'
        )

    serial = resolve_unit_serial(unit_serial)

    with transaction.atomic():
        unit = (
            StockUnit.objects
            .select_for_update(of=('self',))
            .select_related('lot', 'unit', 'location')
            .filter(unit_serial=serial)
            .first()
        )
        if unit is None:
            raise StockValidationError(f'unit_serial={serial} not found')

        existing_entry = (
            StockEntry.objects
            .filter(idempotency_key=idempotency_key)
            .first()
        )
        if existing_entry is not None:
            cons = (
                StockUnitConsumption.objects
                .filter(stock_unit=unit, stock_entry=existing_entry)
                .first()
            )
            if cons is not None:
                unit = (
                    StockUnit.objects
                    .select_related('lot__product', 'unit', 'location')
                    .get(pk=unit.pk)
                )
                return {
                    'entry': existing_entry,
                    'unit': unit,
                    'consumption': cons,
                }
            if unit.status not in _CONSUMABLE_STATUSES:
                raise StockValidationError(
                    f'unit status={unit.status} cannot be consumed'
                )
            if unit.quantity_remaining < quantity:
                raise StockValidationError(
                    f'quantity {quantity} exceeds remaining '
                    f'{unit.quantity_remaining}'
                )
            cons = StockUnitConsumption.objects.create(
                stock_unit=unit,
                stock_entry=existing_entry,
                quantity_base=quantity,
            )
            _apply_unit_drawdown(unit, quantity)
            unit = (
                StockUnit.objects
                .select_related('lot__product', 'unit', 'location')
                .get(pk=unit.pk)
            )
            return {
                'entry': existing_entry,
                'unit': unit,
                'consumption': cons,
            }

        if unit.status not in _CONSUMABLE_STATUSES:
            raise StockValidationError(
                f'unit status={unit.status} cannot be consumed'
            )
        if unit.quantity_remaining < quantity:
            raise StockValidationError(
                f'quantity {quantity} exceeds remaining {unit.quantity_remaining}'
            )

        common = {
            'idempotency_key': idempotency_key,
            'lot': unit.lot,
            'location_id': unit.location_id,
            'quantity': quantity,
            'unit_id': unit.unit_id,
            'effective_at': effective_at,
            **audit_kwargs,
        }
        if entry_type == StockEntryType.ISSUE:
            entry = services.issue(**common)
        elif entry_type == StockEntryType.DISPOSAL:
            entry = services.disposal(**common)
        else:
            entry = services.production_consume(
                output_entry_id=int(output_entry_id),
                **common,
            )

        cons, created = StockUnitConsumption.objects.get_or_create(
            stock_unit=unit,
            stock_entry=entry,
            defaults={
                'quantity_base': (
                    abs(entry.quantity_base)
                    if entry.quantity_base is not None
                    else quantity
                ),
            },
        )
        if created:
            _apply_unit_drawdown(unit, quantity)
        unit = (
            StockUnit.objects
            .select_related('lot__product', 'unit', 'location')
            .get(pk=unit.pk)
        )
        return {'entry': entry, 'unit': unit, 'consumption': cons}


def _apply_unit_drawdown(unit: StockUnit, quantity: Decimal) -> None:
    remaining = unit.quantity_remaining - quantity
    unit.quantity_remaining = remaining
    unit.status = (
        StockUnitStatus.CONSUMED
        if remaining == 0
        else StockUnitStatus.PARTIALLY_CONSUMED
    )
    unit.save(update_fields=['quantity_remaining', 'status'])


def void_unit(
    *,
    unit_serial: str,
    reason: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> StockUnit:
    """Mark a label void. Does not change stock_balance."""
    reason = (reason or '').strip()
    if not reason:
        raise StockValidationError('void reason is required')

    serial = resolve_unit_serial(unit_serial)
    with transaction.atomic():
        unit = (
            StockUnit.objects
            .select_for_update()
            .filter(unit_serial=serial)
            .first()
        )
        if unit is None:
            raise StockValidationError(f'unit_serial={serial} not found')
        if unit.status == StockUnitStatus.VOID:
            return (
                StockUnit.objects
                .select_related('lot__product', 'unit', 'location')
                .get(pk=unit.pk)
            )
        if unit.status == StockUnitStatus.CONSUMED:
            raise StockValidationError('cannot void a fully consumed unit')

        unit.status = StockUnitStatus.VOID
        unit.voided_at = timezone.now()
        unit.void_reason = reason[:255]
        unit.save(update_fields=['status', 'voided_at', 'void_reason'])

    return (
        StockUnit.objects
        .select_related('lot__product', 'unit', 'location')
        .get(pk=unit.pk)
    )


def reprint_unit(
    *,
    unit_serial: str,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
) -> dict:
    """Same serial/payload; append print event reason=reprint."""
    serial = resolve_unit_serial(unit_serial)
    with transaction.atomic():
        unit = (
            StockUnit.objects
            .select_for_update(of=('self',))
            .select_related('lot__product', 'unit', 'location')
            .filter(unit_serial=serial)
            .first()
        )
        if unit is None:
            raise StockValidationError(f'unit_serial={serial} not found')
        if unit.status == StockUnitStatus.VOID:
            raise StockValidationError('cannot reprint a void unit')

        event = StockUnitPrintEvent.objects.create(
            stock_unit=unit,
            printed_at=timezone.now(),
            actor_user_id=actor_user_id,
            lan_username=lan_username,
            source_workstation=source_workstation,
            reason=StockUnitPrintReason.REPRINT,
        )
    return {
        'unit': unit,
        'print_event_id': event.id,
        'gs1': build_gs1_payload(unit),
    }
