from __future__ import annotations

from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.utils import timezone

from product.models import Product, ProductCosting
from product.query import active_products

from stock_ledger.models import StockBalance, StockEntry, StockEntryType, StockGenealogy, StockLot, StockPeriod, StockPeriodStatus
from stock_ledger.stream import publish_balance_delta
from stock_ledger.util.conversions import StockValidationError, resolve_to_kg
from stock_ledger.util.serialize import load_balance_for_row, serialize_balance_row

MYSQL_DUP_ENTRY = 1062

def _is_dup_entry(exc: IntegrityError) -> bool:
    return bool(exc.args) and exc.args[0] == MYSQL_DUP_ENTRY


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
    if not active_products().filter(pk=product_id).exists():
        raise StockValidationError(f'product_id={product_id} is inactive or missing')
    try:
        factor = resolve_to_kg(unit_id=unit_id, product_id=product_id)
    except StockValidationError:
        if Product.objects.filter(pk=product_id, is_downtime=True).exists():
            return None, None
        raise
    return factor, (quantity * factor).quantize(Decimal('0.000001'))


def _existing(idempotency_key: str) -> StockEntry | None:
    return StockEntry.objects.filter(idempotency_key=idempotency_key).first()


def _project_balance(*, entry: StockEntry, override_reason: str | None,) -> StockBalance:
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
    unit_cost: Decimal | None = None,
    line_cost: Decimal | None = None,
    actor_user_id: int | None = None,
    lan_username: str | None = None,
    source_workstation: str | None = None,
    source_workstation_ip: str | None = None,
    remarks: str | None = None,
    project_balance: bool = True,
) -> StockEntry:
    if quantity == 0:
        raise StockValidationError('quantity must be non-zero')

    existing = _existing(idempotency_key)
    if existing is not None:
        return existing

    period = resolve_open_period(effective_at)
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
                unit_cost=unit_cost,
                line_cost=line_cost,
                actor_user_id=actor_user_id,
                lan_username=lan_username,
                source_workstation=source_workstation,
                source_workstation_ip=source_workstation_ip,
                remarks=remarks,
                entry_hash='pending',
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
    unit_id: int,
    effective_at=None,
    unit_cost: Decimal | None = None,
    **kwargs,
) -> StockEntry:
    if quantity <= 0:
        raise StockValidationError('receipt quantity must be positive')
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
    unit_id: int,
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
        unit_id=unit_id,
        effective_at=effective_at or timezone.now(),
        **kwargs,
    )


def disposal(
    *,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    quantity: Decimal,
    unit_id: int,
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
        unit_id=unit_id,
        effective_at=effective_at or timezone.now(),
        **kwargs,
    )


def count_adjustment(
    *,
    idempotency_key: str,
    lot: StockLot,
    location_id: int,
    quantity_delta: Decimal,
    unit_id: int,
    effective_at=None,
    **kwargs,
) -> StockEntry:
    if quantity_delta == 0:
        raise StockValidationError('count_adjustment delta must be non-zero')
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
    unit_id: int,
    effective_at=None,
    **kwargs,
) -> tuple[StockEntry, StockEntry]:
    if quantity <= 0:
        raise StockValidationError('transfer quantity must be positive')
    if from_location_id == to_location_id:
        raise StockValidationError('transfer locations must differ')

    out_key = f'{idempotency_key}:out'
    in_key = f'{idempotency_key}:in'
    existing_out = _existing(out_key)
    existing_in = _existing(in_key)
    if existing_out and existing_in:
        return existing_out, existing_in

    effective_at = effective_at or timezone.now()
    group_id = str(uuid4())

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
    return out_entry, in_entry


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
        output = _insert_entry( idempotency_key=out_key, entry_type=StockEntryType.PRODUCTION_OUTPUT, lot=output_lot, location_id=output_location_id, quantity=output_quantity, unit_id=output_unit_id, effective_at=effective_at, **kwargs)
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
