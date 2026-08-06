from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from product.models import ProductCosting
from product.query import active_products
from planning.models import Resource
from recipe.models import RecipeComponent, RecipeVersion, RecipeVersionStatus

from stock_ledger.models import (
    ProductionRun,
    StockBalance,
    StockEntry,
    StockEntryType,
    StockGenealogy,
    StockLot,
    StockLotOrigin,
    StockPeriod,
    StockPeriodStatus,
)
from stock_ledger.stream import publish_balance_delta
from stock_ledger.util.conversions import StockValidationError, resolve_to_kg
from stock_ledger.util.serialize import (
    BALANCE_SELECT_RELATED,
    load_balance_for_row,
    serialize_balance_row,
)

MYSQL_DUP_ENTRY = 1062
PRODUCTION_SOURCE_DOC = 'production_output'


def _is_dup_entry(exc: IntegrityError) -> bool:
    return bool(exc.args) and exc.args[0] == MYSQL_DUP_ENTRY


def julian_trace_number(day: date) -> str:
    """YY + day-of-year, e.g. 2026-05-05 → 26125."""
    return f'{day.year % 100:02d}{day.timetuple().tm_yday:03d}'


def resolve_lot(
    *,
    product_id: int,
    trace_number: str | None = None,
    use_by: date | None = None,
    production_date: date | None = None,
    recipe_version_id: int | None = None,
    shape_format_id: int | None = None,
    origin: str = StockLotOrigin.PURCHASE,
    supplier_lot_code: str | None = None,
) -> StockLot:
    """
    Get-or-create StockLot from soft identity attrs.

    Floor/ops pass product + use_by + trace; lot row stays internal.
    # ponytail: keep StockLot table; drop when balance key moves off lot_id
    """
    if not active_products().filter(pk=product_id).exists():
        raise StockValidationError(f'product_id={product_id} is inactive or missing')
    if origin not in StockLotOrigin.values:
        raise StockValidationError(
            f'Invalid origin. Use one of: {", ".join(StockLotOrigin.values)}'
        )

    if recipe_version_id not in (None, ''):
        recipe_version_id = int(recipe_version_id)
        belongs = RecipeVersion.objects.filter(
            pk=recipe_version_id,
            recipe__product_id=product_id,
        ).exists()
        if not belongs:
            raise StockValidationError(
                f'recipe_version_id={recipe_version_id} '
                f'does not belong to product_id={product_id}'
            )
    elif origin == StockLotOrigin.PRODUCTION:
        # Stamp active recipe at MADE time — BOM is frozen on the lot.
        active = (
            RecipeVersion.objects
            .filter(
                recipe__product_id=product_id,
                status=RecipeVersionStatus.ACTIVE,
            )
            .order_by('-version_number')
            .values_list('id', flat=True)
            .first()
        )
        if active is None:
            raise StockValidationError(
                f'No active recipe version for product_id={product_id}'
            )
        recipe_version_id = active
    else:
        recipe_version_id = None

    if trace_number in (None, ''):
        day = production_date or timezone.localdate()
        trace_number = julian_trace_number(day)
        if production_date is None:
            production_date = day
    else:
        trace_number = str(trace_number)

    lookup = {
        'product_id': product_id,
        'trace_number': trace_number,
        'production_date': production_date,
        'use_by': use_by,
        'recipe_version_id': recipe_version_id,
        'shape_format_id': shape_format_id,
    }
    defaults = {
        'origin': origin,
        'supplier_lot_code': supplier_lot_code,
    }
    try:
        lot, _ = StockLot.objects.get_or_create(**lookup, defaults=defaults)
        return lot
    except IntegrityError as exc:
        if not _is_dup_entry(exc):
            raise
        lot = StockLot.objects.filter(**lookup).first()
        if lot is None:
            raise
        return lot


def resolve_open_period(effective_at):
    day = timezone.localtime(effective_at).date() if timezone.is_aware(effective_at) else effective_at.date()
    period = StockPeriod.objects.filter(
        period_start__lte=day,
        period_end__gte=day,
        status=StockPeriodStatus.OPEN,
    ).first()
    if period is None:
        raise StockValidationError(f'No open stock_period for date={day}')
    return period


def _mass_fields(*, product_id: int, unit_id: int, quantity: Decimal):
    """Resolve kg base when conversion exists; else leave null (MVP: stock unit only)."""
    if not active_products().filter(pk=product_id).exists():
        raise StockValidationError(f'product_id={product_id} is inactive or missing')
    try:
        factor = resolve_to_kg(unit_id=unit_id, product_id=product_id)
    except StockValidationError:
        # ponytail: no fail-loud kg; add when mass-balance needs it
        return None, None
    return factor, (quantity * factor).quantize(Decimal('0.000001'))


def _resolve_unit_id(lot: StockLot, unit_id: int | None) -> int:
    if unit_id is not None:
        return unit_id
    if lot.product.unit_id is None:
        raise StockValidationError(f'product_id={lot.product_id} has no stock unit')
    return lot.product.unit_id


def _existing(idempotency_key: str) -> StockEntry | None:
    return StockEntry.objects.filter(idempotency_key=idempotency_key).first()


def _project_balance(*, entry: StockEntry, override_reason: str | None,) -> StockBalance | None:
    balance = (
        StockBalance.objects
        .select_for_update()
        .filter(lot_id=entry.lot_id, location_id=entry.location_id)
        .first()
    )
    new_qty = entry.quantity if balance is None else balance.quantity + entry.quantity
    new_base = None
    if entry.quantity_base is not None:
        prev_base = Decimal('0') if balance is None or balance.quantity_base is None else balance.quantity_base
        new_base = prev_base + entry.quantity_base

    neg_auth_id = None
    if new_qty < 0:
        if not (override_reason and entry.authorised_by_user_id):
            raise StockValidationError(
                'stock_balance: negative without authorised override'
            )
        neg_auth_id = entry.id

    if new_qty == 0:
        if balance is None:
            return None
        balance.quantity = Decimal('0')
        _schedule_balance_stream(balance)
        balance.delete()
        return None

    if balance is None:
        balance = StockBalance.objects.create(
            lot_id=entry.lot_id,
            location_id=entry.location_id,
            quantity=new_qty,
            quantity_base=new_base,
            last_entry_id=entry.id,
            last_count_entry_id=(
                entry.id
                if entry.entry_type == StockEntryType.COUNT_ADJUSTMENT
                else None
            ),
            negative_authorised_by_entry_id=neg_auth_id,
            updated_at=timezone.now(),
        )
        _schedule_balance_stream(balance)
        return balance

    balance.quantity = new_qty
    balance.quantity_base = new_base
    balance.last_entry_id = entry.id
    balance.updated_at = timezone.now()
    balance.negative_authorised_by_entry_id = neg_auth_id
    if entry.entry_type == StockEntryType.COUNT_ADJUSTMENT:
        balance.last_count_entry_id = entry.id
    balance.save()
    _schedule_balance_stream(balance)
    return balance


def _schedule_balance_stream(balance: StockBalance) -> None:
    """Publish upsert/remove after commit. Build payload now; fan-out stays off hot path."""
    lot_id = balance.lot_id
    location_id = balance.location_id
    at = timezone.now().isoformat()
    if balance.quantity == 0:
        event = {
            'type': 'remove',
            'at': at,
            'row': {'lot_id': lot_id, 'location_id': location_id},
        }
    else:
        row_balance = load_balance_for_row(lot_id=lot_id, location_id=location_id)
        if row_balance is None:
            return
        event = {
            'type': 'upsert',
            'at': at,
            'row': serialize_balance_row(row_balance),
        }
    transaction.on_commit(lambda e=event: publish_balance_delta(e))


def _insert_entry(
    *,
    idempotency_key: str,
    entry_type: str,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    unit_id: int,
    effective_at,
    counterparty_location_id: int | None = None,
    transfer_group_id: str | None = None,
    reverses_entry: StockEntry | None = None,
    override_reason: str | None = None,
    authorised_by_user_id: int | None = None,
    source_document_type: str | None = None,
    source_document_id: int | None = None,
    source_document_line: int | None = None,
    po_number: str | None = None,
    unit_cost: Decimal | None = None,
    line_cost: Decimal | None = None,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
    source_workstation_ip: str | None = None,
    remarks: str | None = None,
    project_balance: bool = True,
) -> StockEntry:
    if quantity == 0 and entry_type != StockEntryType.DOWNTIME:
        raise StockValidationError('quantity must be non-zero')

    existing = _existing(idempotency_key)
    if existing is not None:
        return existing

    period = resolve_open_period(effective_at)
    if entry_type == StockEntryType.DOWNTIME:
        factor, qty_base = None, None
        project_balance = False
    else:
        factor, qty_base = _mass_fields(
            product_id=lot.product_id, unit_id=unit_id, quantity=quantity,
        )

    try:
        with transaction.atomic():
            entry = StockEntry.objects.create(
                idempotency_key=idempotency_key,
                entry_type=entry_type,
                lot=lot,
                location_id=location_id,
                counterparty_location_id=counterparty_location_id,
                transfer_group_id=transfer_group_id,
                quantity=quantity,
                unit_id=unit_id,
                base_unit_factor=factor,
                quantity_base=qty_base,
                period=period,
                effective_at=effective_at,
                recorded_at=timezone.now(),
                reverses_entry=reverses_entry,
                override_reason=override_reason,
                authorised_by_user_id=authorised_by_user_id,
                source_document_type=source_document_type,
                source_document_id=source_document_id,
                source_document_line=source_document_line,
                po_number=po_number,
                unit_cost=unit_cost,
                line_cost=line_cost,
                actor_user_id=actor_user_id,
                lan_username=lan_username,
                source_workstation=source_workstation,
                source_workstation_ip=source_workstation_ip,
                remarks=remarks,
                # Overwritten by the stock_entry_bi trigger. Derived from the
                # idempotency key so the unique column never collides.
                entry_hash=hashlib.sha256(
                    idempotency_key.encode('utf-8'),
                ).hexdigest(),
            )
            if project_balance:
                _project_balance(entry=entry, override_reason=override_reason)
            return entry
    except IntegrityError as exc:
        if _is_dup_entry(exc):
            existing = _existing(idempotency_key)
            if existing is not None:
                return existing
        raise


def receipt(
    *,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    unit_id: int | None = None,
    effective_at=None,
    unit_cost: Decimal | None = None,
    **kwargs,
) -> StockEntry:
    if quantity <= 0:
        raise StockValidationError('receipt quantity must be positive')
    unit_id = _resolve_unit_id(lot, unit_id)
    effective_at = effective_at or timezone.now()
    if unit_cost is None:
        costing = ProductCosting.objects.filter(product_id=lot.product_id).first()
        if costing is not None:
            unit_cost = costing.unit_cost
    line_cost = (unit_cost * quantity) if unit_cost is not None else None
    return _insert_entry(
        idempotency_key=idempotency_key,
        entry_type=StockEntryType.RECEIPT,
        lot=lot,
        location_id=location_id,
        quantity=quantity,
        unit_id=unit_id,
        effective_at=effective_at,
        unit_cost=unit_cost,
        line_cost=line_cost,
        **kwargs,
    )


def issue(
    *,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    unit_id: int | None = None,
    effective_at=None,
    **kwargs,
) -> StockEntry:
    if quantity <= 0:
        raise StockValidationError('issue quantity must be positive')
    return _insert_entry(
        idempotency_key=idempotency_key,
        entry_type=StockEntryType.ISSUE,
        lot=lot,
        location_id=location_id,
        quantity=-quantity,
        unit_id=_resolve_unit_id(lot, unit_id),
        effective_at=effective_at or timezone.now(),
        **kwargs,
    )


def disposal(
    *,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    unit_id: int | None = None,
    effective_at=None,
    **kwargs,
) -> StockEntry:
    if quantity <= 0:
        raise StockValidationError('disposal quantity must be positive')
    return _insert_entry(
        idempotency_key=idempotency_key,
        entry_type=StockEntryType.DISPOSAL,
        lot=lot,
        location_id=location_id,
        quantity=-quantity,
        unit_id=_resolve_unit_id(lot, unit_id),
        effective_at=effective_at or timezone.now(),
        **kwargs,
    )


def count_adjustment(
    *,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    quantity_delta: Decimal | None = None,
    counted_quantity: Decimal | None = None,
    unit_id: int | None = None,
    effective_at=None,
    **kwargs,
) -> StockEntry:
    unit_id = _resolve_unit_id(lot, unit_id)
    if counted_quantity is not None:
        balance = (
            StockBalance.objects
            .filter(lot_id=lot.id, location_id=location_id)
            .only('quantity')
            .first()
        )
        current = balance.quantity if balance is not None else Decimal('0')
        quantity_delta = counted_quantity - current
    if quantity_delta is None:
        raise StockValidationError(
            'counted_quantity or quantity_delta is required'
        )
    if quantity_delta == 0:
        raise StockValidationError(
            'count_adjustment delta must be non-zero (counted matches on-hand)'
        )
    return _insert_entry(
        idempotency_key=idempotency_key,
        entry_type=StockEntryType.COUNT_ADJUSTMENT,
        lot=lot,
        location_id=location_id,
        quantity=quantity_delta,
        unit_id=unit_id,
        effective_at=effective_at or timezone.now(),
        **kwargs,
    )


def transfer(
    *,
    idempotency_key: str,
    lot: StockLot,
    from_location_id: int,
    to_location_id: int,
    quantity: Decimal,
    unit_id: int | None = None,
    effective_at=None,
    unit_moves: list | None = None,
    **kwargs,
) -> tuple[StockEntry, StockEntry]:
    if quantity <= 0:
        raise StockValidationError('transfer quantity must be positive')
    if from_location_id == to_location_id:
        raise StockValidationError('transfer locations must differ')
    unit_id = _resolve_unit_id(lot, unit_id)

    out_key = f'{idempotency_key}:out'
    in_key = f'{idempotency_key}:in'
    existing_out = _existing(out_key)
    existing_in = _existing(in_key)
    if existing_out and existing_in:
        return existing_out, existing_in

    effective_at = effective_at or timezone.now()
    group_id = str(uuid4())

    # Local import: stock_units imports services (consume path).
    from stock_ledger.util.unit_moves import apply_unit_moves_for_transfer

    with transaction.atomic():
        out_entry = _insert_entry(
            idempotency_key=out_key,
            entry_type=StockEntryType.TRANSFER_OUT,
            lot=lot,
            location_id=from_location_id,
            counterparty_location_id=to_location_id,
            transfer_group_id=group_id,
            quantity=-quantity,
            unit_id=unit_id,
            effective_at=effective_at,
            **kwargs,
        )
        in_entry = _insert_entry(
            idempotency_key=in_key,
            entry_type=StockEntryType.TRANSFER_IN,
            lot=lot,
            location_id=to_location_id,
            counterparty_location_id=from_location_id,
            transfer_group_id=out_entry.transfer_group_id or group_id,
            quantity=quantity,
            unit_id=unit_id,
            effective_at=effective_at,
            **kwargs,
        )
        if unit_moves is not None:
            apply_unit_moves_for_transfer(
                lot_id=lot.id,
                from_location_id=from_location_id,
                to_location_id=to_location_id,
                quantity=quantity,
                unit_moves=unit_moves,
            )
    return out_entry, in_entry


def record_downtime(
    *,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    resource_id: int,
    base_date,
    unit_id: int | None = None,
    counterparty_location_id: int | None = None,
    shift_code: str | None = None,
    staff_count: int | None = None,
    started_at=None,
    finished_at=None,
    effective_at=None,
    **kwargs,
) -> tuple[StockEntry, ProductionRun]:
    """Floor downtime time-row: qty 0, no balance update. Same sidecar as production."""
    if not lot.product.is_downtime:
        raise StockValidationError(
            f'product_id={lot.product_id} is not a downtime type; use POST /stock/production/'
        )
    if not Resource.objects.filter(pk=resource_id, is_active=True).exists():
        raise StockValidationError(f'resource_id={resource_id} not found or inactive')
    if started_at is not None and finished_at is not None and finished_at <= started_at:
        raise StockValidationError('finished_at must be after started_at')
    if unit_id is None:
        unit_id = lot.product.unit_id
        if unit_id is None:
            raise StockValidationError(
                f'product_id={lot.product_id} has no stock unit'
            )

    existing = _existing(idempotency_key)
    if existing is not None:
        try:
            return existing, existing.production_run
        except ProductionRun.DoesNotExist as exc:
            raise StockValidationError(
                f'idempotency_key={idempotency_key} exists without production_run'
            ) from exc

    with transaction.atomic():
        entry = _insert_entry(
            idempotency_key=idempotency_key,
            entry_type=StockEntryType.DOWNTIME,
            lot=lot,
            location_id=location_id,
            counterparty_location_id=counterparty_location_id,
            quantity=Decimal('0'),
            unit_id=unit_id,
            effective_at=effective_at or timezone.now(),
            project_balance=False,
            **kwargs,
        )
        run, _ = ProductionRun.objects.get_or_create(
            stock_entry=entry,
            defaults={
                'resource_id': resource_id,
                'shift_code': shift_code or None,
                'staff_count': staff_count,
                'base_date': base_date,
                'started_at': started_at,
                'finished_at': finished_at,
            },
        )
    return entry, run


def production_output(
    *,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    resource_id: int,
    base_date,
    unit_id: int | None = None,
    counterparty_location_id: int | None = None,
    shift_code: str | None = None,
    staff_count: int | None = None,
    started_at=None,
    finished_at=None,
    effective_at=None,
    **kwargs,
) -> tuple[StockEntry, ProductionRun]:
    """Floor production stock-in + run sidecar. Component consume is chunk 6."""
    if lot.product.is_downtime:
        raise StockValidationError(
            f'product_id={lot.product_id} is downtime; use POST /stock/downtime/'
        )
    if quantity <= 0:
        raise StockValidationError('production output quantity must be positive')
    if not Resource.objects.filter(pk=resource_id, is_active=True).exists():
        raise StockValidationError(f'resource_id={resource_id} not found or inactive')
    if unit_id is None:
        unit_id = lot.product.unit_id
        if unit_id is None:
            raise StockValidationError(
                f'product_id={lot.product_id} has no stock unit'
            )

    existing = _existing(idempotency_key)
    if existing is not None:
        try:
            return existing, existing.production_run
        except ProductionRun.DoesNotExist as exc:
            raise StockValidationError(
                f'idempotency_key={idempotency_key} exists without production_run'
            ) from exc

    with transaction.atomic():
        entry = _insert_entry(
            idempotency_key=idempotency_key,
            entry_type=StockEntryType.PRODUCTION_OUTPUT,
            lot=lot,
            location_id=location_id,
            counterparty_location_id=counterparty_location_id,
            quantity=quantity,
            unit_id=unit_id,
            effective_at=effective_at or timezone.now(),
            **kwargs,
        )
        run, _ = ProductionRun.objects.get_or_create(
            stock_entry=entry,
            defaults={
                'resource_id': resource_id,
                'shift_code': shift_code or None,
                'staff_count': staff_count,
                'base_date': base_date,
                'started_at': started_at,
                'finished_at': finished_at,
            },
        )
    return entry, run


def _entry_is_reversed(entry: StockEntry) -> bool:
    return StockEntry.objects.filter(reverses_entry_id=entry.pk).exists()


@transaction.atomic
def production_replace(
    *,
    entry_id: int,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    resource_id: int,
    base_date,
    unit_id: int | None = None,
    counterparty_location_id: int | None = None,
    shift_code: str | None = None,
    staff_count: int | None = None,
    started_at=None,
    finished_at=None,
    effective_at=None,
    **kwargs,
) -> tuple[StockEntry, ProductionRun]:
    """
    Edit production: reverse prior allocates + output, then post corrected output.
    Idempotent on idempotency_key (returns the replacement output if already posted).
    """
    existing = _existing(idempotency_key)
    if existing is not None:
        if existing.entry_type != StockEntryType.PRODUCTION_OUTPUT:
            raise StockValidationError(
                f'idempotency_key={idempotency_key} exists as {existing.entry_type}'
            )
        try:
            return existing, existing.production_run
        except ProductionRun.DoesNotExist as exc:
            raise StockValidationError(
                f'idempotency_key={idempotency_key} exists without production_run'
            ) from exc

    output = _require_production_output(entry_id)
    if _entry_is_reversed(output):
        raise StockValidationError(f'entry_id={entry_id} is already reversed')

    # Undo floor allocates tied to this output first.
    consumptions = (
        StockEntry.objects
        .filter(
            entry_type=StockEntryType.PRODUCTION_CONSUMPTION,
            source_document_type=PRODUCTION_SOURCE_DOC,
            source_document_id=output.id,
        )
        .order_by('id')
    )
    for cons in consumptions:
        if _entry_is_reversed(cons):
            continue
        reversal(
            idempotency_key=f'{idempotency_key}:consume:{cons.id}',
            entry=cons,
            effective_at=effective_at,
            **kwargs,
        )

    reversal(
        idempotency_key=f'{idempotency_key}:reverse',
        entry=output,
        effective_at=effective_at,
        **kwargs,
    )
    return production_output(
        idempotency_key=idempotency_key,
        lot=lot,
        location_id=location_id,
        quantity=quantity,
        resource_id=resource_id,
        base_date=base_date,
        unit_id=unit_id,
        counterparty_location_id=counterparty_location_id,
        shift_code=shift_code,
        staff_count=staff_count,
        started_at=started_at,
        finished_at=finished_at,
        effective_at=effective_at,
        **kwargs,
    )


@transaction.atomic
def production_void(
    *,
    entry_id: int,
    idempotency_key: str,
    effective_at=None,
    **kwargs,
) -> StockEntry:
    """Reverse production output and any allocate consumptions linked to it."""
    existing = _existing(idempotency_key)
    if existing is not None:
        if existing.entry_type != StockEntryType.REVERSAL:
            raise StockValidationError(
                f'idempotency_key={idempotency_key} exists as {existing.entry_type}'
            )
        return existing

    output = _require_production_output(entry_id)
    if _entry_is_reversed(output):
        raise StockValidationError(f'entry_id={entry_id} is already reversed')

    consumptions = (
        StockEntry.objects
        .filter(
            entry_type=StockEntryType.PRODUCTION_CONSUMPTION,
            source_document_type=PRODUCTION_SOURCE_DOC,
            source_document_id=output.id,
        )
        .order_by('id')
    )
    for cons in consumptions:
        if _entry_is_reversed(cons):
            continue
        reversal(
            idempotency_key=f'{idempotency_key}:consume:{cons.id}',
            entry=cons,
            effective_at=effective_at,
            **kwargs,
        )

    return reversal(
        idempotency_key=idempotency_key,
        entry=output,
        effective_at=effective_at,
        **kwargs,
    )


def production(
    *,
    idempotency_key: str,
    output_lot: StockLot,
    output_location_id: int,
    output_quantity: Decimal,
    output_unit_id: int,
    inputs: Iterable[dict],
    effective_at=None,
    **kwargs,
) -> tuple[StockEntry, list[StockEntry]]:
    """
    inputs: iterable of dicts with keys:
      lot, location_id, quantity (>0), unit_id, optional genealogy_quantity_base
    """
    if output_quantity <= 0:
        raise StockValidationError('production output quantity must be positive')

    out_key = f'{idempotency_key}:out'
    existing_out = _existing(out_key)
    if existing_out is not None:
        consumptions = list(
            StockEntry.objects.filter(
                idempotency_key__startswith=f'{idempotency_key}:in:',
            ).order_by('id')
        )
        return existing_out, consumptions

    effective_at = effective_at or timezone.now()
    consumptions: list[StockEntry] = []

    with transaction.atomic():
        output = _insert_entry(
            idempotency_key=out_key,
            entry_type=StockEntryType.PRODUCTION_OUTPUT,
            lot=output_lot,
            location_id=output_location_id,
            quantity=output_quantity,
            unit_id=output_unit_id,
            effective_at=effective_at,
            **kwargs,
        )
        for i, raw in enumerate(inputs):
            qty = Decimal(str(raw['quantity']))
            if qty <= 0:
                raise StockValidationError('production input quantity must be positive')
            consumption = _insert_entry(
                idempotency_key=f'{idempotency_key}:in:{i}',
                entry_type=StockEntryType.PRODUCTION_CONSUMPTION,
                lot=raw['lot'],
                location_id=raw['location_id'],
                quantity=-qty,
                unit_id=raw['unit_id'],
                effective_at=effective_at,
                **kwargs,
            )
            gene_qty = raw.get('genealogy_quantity_base')
            if gene_qty is None:
                gene_qty = consumption.quantity_base
            if gene_qty is None:
                raise StockValidationError(
                    'genealogy quantity_base required for production edge'
                )
            gene_qty = abs(Decimal(str(gene_qty)))
            if gene_qty <= 0:
                raise StockValidationError(
                    'genealogy quantity_base must be positive'
                )
            StockGenealogy.objects.get_or_create(
                output_entry=output,
                input_entry=consumption,
                defaults={'quantity_base': gene_qty},
            )
            consumptions.append(consumption)
    return output, consumptions


def _require_production_output(entry_id: int) -> StockEntry:
    try:
        entry = (
            StockEntry.objects
            .select_related('lot__product', 'lot__recipe_version')
            .get(pk=entry_id)
        )
    except StockEntry.DoesNotExist as exc:
        raise StockValidationError(f'entry_id={entry_id} not found') from exc
    if entry.entry_type != StockEntryType.PRODUCTION_OUTPUT:
        raise StockValidationError(
            f'entry_id={entry_id} is not production_output'
        )
    return entry


def _resolve_recipe_version(output: StockEntry) -> RecipeVersion:
    """BOM is frozen on the lot at MADE — never swap to a different version later."""
    lot = output.lot
    if not lot.recipe_version_id:
        raise StockValidationError(
            f'lot_id={lot.id} has no recipe_version '
            f'(product_id={lot.product_id}); reverse and remake to stamp BOM'
        )
    version = lot.recipe_version
    recipe_product_id = (
        RecipeVersion.objects
        .filter(pk=version.id)
        .values_list('recipe__product_id', flat=True)
        .first()
    )
    if recipe_product_id != lot.product_id:
        raise StockValidationError(
            f'lot_id={lot.id} recipe_version_id={version.id} belongs to '
            f'product_id={recipe_product_id}, not product_id={lot.product_id}'
        )
    return version


def _component_yield_factor(product) -> Decimal:
    try:
        factor = product.yield_data.yield_factor
    except ObjectDoesNotExist:
        return Decimal('1')
    if factor is None or factor <= 0:
        return Decimal('1')
    return factor


def _consumed_by_product(output_entry_id: int) -> dict[int, Decimal]:
    rows = (
        StockEntry.objects
        .filter(
            entry_type=StockEntryType.PRODUCTION_CONSUMPTION,
            source_document_type=PRODUCTION_SOURCE_DOC,
            source_document_id=output_entry_id,
        )
        .values('lot__product_id')
        .annotate(total=Sum('quantity'))
    )
    # consumption quantities are negative
    return {
        row['lot__product_id']: abs(row['total'] or Decimal('0'))
        for row in rows
        if row['lot__product_id'] is not None
    }


def production_requirements(
    *,
    output_entry_id: int,
    location_id: int | None = None,
) -> dict:
    """
    One-level recipe explode for a production output entry.
    needed = (made / process_loss) * component.qty / component_yield
    """
    output = _require_production_output(output_entry_id)
    version = _resolve_recipe_version(output)
    process_loss = version.process_loss or Decimal('1')
    if process_loss <= 0:
        process_loss = Decimal('1')
    made = abs(output.quantity)
    parent_gross = made / process_loss
    consumed_map = _consumed_by_product(output.id)

    components = (
        RecipeComponent.objects
        .filter(recipe_version_id=version.id)
        .select_related('component_product__yield_data', 'unit')
        .order_by('line_no')
    )
    lines = []
    product_ids = []
    for comp in components:
        yf = _component_yield_factor(comp.component_product)
        needed = (parent_gross * comp.quantity / yf).quantize(Decimal('0.000001'))
        consumed = consumed_map.get(comp.component_product_id, Decimal('0'))
        product_ids.append(comp.component_product_id)
        lines.append({
            'component_product_id': comp.component_product_id,
            'component_product_name': comp.component_product.name,
            'recipe_quantity': str(comp.quantity),
            'unit_id': comp.unit_id,
            'yield_factor': str(yf),
            'needed_quantity': str(needed),
            'consumed_quantity': str(consumed),
            'remaining_quantity': str(
                max(needed - consumed, Decimal('0')).quantize(Decimal('0.000001'))
            ),
            'balances': [],
        })

    if location_id is not None and product_ids:
        balances = (
            StockBalance.objects
            .filter(
                location_id=location_id,
                lot__product_id__in=product_ids,
                quantity__gt=0,
            )
            .select_related(*BALANCE_SELECT_RELATED)
            .order_by('lot__product_id', 'lot_id')
        )
        by_product: dict[int, list] = {}
        for bal in balances:
            by_product.setdefault(bal.lot.product_id, []).append(
                serialize_balance_row(bal)
            )
        for line in lines:
            line['balances'] = by_product.get(line['component_product_id'], [])

    return {
        'output_entry_id': output.id,
        'product_id': output.lot.product_id,
        'made_quantity': str(made),
        'recipe_version_id': version.id,
        'process_loss': str(process_loss),
        'location_id': location_id,
        'components': lines,
    }


def production_consume(
    *,
    idempotency_key: str,
    output_entry_id: int,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    unit_id: int | None = None,
    effective_at=None,
    **kwargs,
) -> StockEntry:
    """Stock-out a component pile against a production output (floor allocate)."""
    if quantity <= 0:
        raise StockValidationError('consume quantity must be positive')
    output = _require_production_output(output_entry_id)
    unit_id = _resolve_unit_id(lot, unit_id)
    effective_at = effective_at or timezone.now()

    existing = _existing(idempotency_key)
    if existing is not None:
        return existing

    with transaction.atomic():
        consumption = _insert_entry(
            idempotency_key=idempotency_key,
            entry_type=StockEntryType.PRODUCTION_CONSUMPTION,
            lot=lot,
            location_id=location_id,
            quantity=-quantity,
            unit_id=unit_id,
            effective_at=effective_at,
            source_document_type=PRODUCTION_SOURCE_DOC,
            source_document_id=output.id,
            **kwargs,
        )
        gene_qty = consumption.quantity_base
        if gene_qty is None:
            gene_qty = quantity  # ponytail: stock-unit qty when no kg factor
        gene_qty = abs(Decimal(str(gene_qty)))
        StockGenealogy.objects.get_or_create(
            output_entry=output,
            input_entry=consumption,
            defaults={'quantity_base': gene_qty},
        )
    return consumption


def reversal(*, idempotency_key: str, entry: StockEntry, effective_at=None, actor_user_id: int | None = None, **kwargs) -> StockEntry:
    if hasattr(entry, 'reversed_by'):
        try:
            return entry.reversed_by
        except StockEntry.DoesNotExist:
            pass
    return _insert_entry(
        idempotency_key=idempotency_key,
        entry_type=StockEntryType.REVERSAL,
        lot=entry.lot,
        location_id=entry.location_id,
        counterparty_location_id=entry.counterparty_location_id,
        transfer_group_id=entry.transfer_group_id,
        quantity=-entry.quantity,
        unit_id=entry.unit_id,
        effective_at=effective_at or timezone.now(),
        reverses_entry=entry,
        actor_user_id=actor_user_id,
        **kwargs,
    )
