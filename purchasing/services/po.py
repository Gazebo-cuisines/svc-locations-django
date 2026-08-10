from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Prefetch

from locations.models import Location, LocationRole
from product.models import Product, ProductSupplier
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderSource,
    PurchaseOrderStatus,
)


class PoValidationError(ValueError):
    pass


def assign_po_number(po: PurchaseOrder) -> str:
    number = f'PO{po.id}'
    po.number = number
    po.save(update_fields=['number', 'updated_at'])
    return number


def _require_supplier(supplier_id: int) -> Location:
    try:
        location = Location.objects.get(pk=supplier_id)
    except Location.DoesNotExist as exc:
        raise PoValidationError(f'supplier_id={supplier_id} not found.') from exc
    if not location.roles.filter(role=LocationRole.SUPPLIER).exists():
        raise PoValidationError(f'supplier_id={supplier_id} is not a supplier.')
    return location


def _optional_location(location_id, field_name: str) -> Location | None:
    if location_id in (None, ''):
        return None
    try:
        return Location.objects.get(pk=int(location_id))
    except (Location.DoesNotExist, TypeError, ValueError) as exc:
        raise PoValidationError(f'{field_name}={location_id} not found.') from exc


def _parse_decimal(value, field_name: str) -> Decimal:
    try:
        qty = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PoValidationError(f'Invalid decimal for {field_name}.') from exc
    if qty <= 0:
        raise PoValidationError(f'{field_name} must be greater than 0.')
    return qty


def _parse_optional_date(value, field_name: str) -> date | None:
    if value in (None, ''):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise PoValidationError(
            f'Invalid date for {field_name}. Use YYYY-MM-DD.',
        ) from exc


def _build_line_rows(supplier_id: int, lines: list) -> list[dict]:
    if not isinstance(lines, list) or not lines:
        raise PoValidationError('lines must be a non-empty list.')

    rows = []
    for index, raw in enumerate(lines, start=1):
        if not isinstance(raw, dict):
            raise PoValidationError(f'lines[{index}] must be an object.')
        product_id = raw.get('product_id')
        if product_id in (None, ''):
            raise PoValidationError(f'lines[{index}].product_id is required.')
        try:
            product = Product.objects.get(pk=int(product_id), is_active=True)
        except (Product.DoesNotExist, TypeError, ValueError) as exc:
            raise PoValidationError(
                f'lines[{index}].product_id={product_id} not found.',
            ) from exc

        qty_ordered = _parse_decimal(raw.get('qty_ordered'), f'lines[{index}].qty_ordered')
        product_supplier = None
        ps_id = raw.get('product_supplier_id')
        if ps_id not in (None, ''):
            try:
                product_supplier = ProductSupplier.objects.select_related(
                    'outer_unit', 'inner_unit',
                ).get(pk=int(ps_id), product_id=product.id, is_active=True)
            except (ProductSupplier.DoesNotExist, TypeError, ValueError) as exc:
                raise PoValidationError(
                    f'lines[{index}].product_supplier_id={ps_id} not found '
                    f'for product {product.id}.',
                ) from exc
            if product_supplier.supplier_id != supplier_id:
                raise PoValidationError(
                    f'lines[{index}].product_supplier_id does not belong to '
                    f'supplier_id={supplier_id}.',
                )
        else:
            product_supplier = (
                ProductSupplier.objects.filter(
                    product_id=product.id,
                    supplier_id=supplier_id,
                    is_active=True,
                )
                .order_by('-is_default', 'id')
                .first()
            )

        unit_id = product.purchasing_unit_id or product.unit_id
        unit_cost = None
        multiplier = None
        shape_format_label = None
        if product_supplier is not None:
            unit_id = product_supplier.inner_unit_id or unit_id
            unit_cost = product_supplier.cost
            multiplier = product_supplier.multiplier
            shape_format_label = product_supplier.shape_format_label

        if 'unit_cost' in raw and raw.get('unit_cost') not in (None, ''):
            unit_cost = _parse_decimal(raw.get('unit_cost'), f'lines[{index}].unit_cost')

        line_no = raw.get('line_no', index)
        try:
            line_no = int(line_no)
        except (TypeError, ValueError) as exc:
            raise PoValidationError(
                f'lines[{index}].line_no must be an integer.',
            ) from exc

        rows.append({
            'line_no': line_no,
            'product_id': product.id,
            'product_supplier_id': (
                product_supplier.id if product_supplier is not None else None
            ),
            'qty_ordered': qty_ordered,
            'qty_received': Decimal('0'),
            'qty_balance': qty_ordered,
            'unit_id': unit_id,
            'unit_cost': unit_cost,
            'multiplier': multiplier,
            'shape_format_label': shape_format_label,
            'remarks': raw.get('remarks') or None,
        })

    line_nos = [row['line_no'] for row in rows]
    if len(line_nos) != len(set(line_nos)):
        raise PoValidationError('Duplicate line_no in lines.')
    return rows


@transaction.atomic
def create_purchase_order(
    *,
    supplier_id: int,
    lines: list,
    ship_to_location_id=None,
    expected_at=None,
    ordered_at=None,
    remarks=None,
    created_by_user_id=None,
    status: str = PurchaseOrderStatus.DRAFT,
    source: str = PurchaseOrderSource.MANUAL,
    external_number: str | None = None,
) -> PurchaseOrder:
    supplier = _require_supplier(supplier_id)
    ship_to = _optional_location(ship_to_location_id, 'ship_to_location_id')
    if status not in (PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.ORDERED):
        raise PoValidationError('Create status must be draft or ordered.')
    if source not in PurchaseOrderSource.values:
        raise PoValidationError(
            f'Invalid source. Use one of: {", ".join(PurchaseOrderSource.values)}.',
        )
    if external_number in ('',):
        external_number = None
    if external_number is not None:
        external_number = str(external_number).strip()[:64]
        exists = PurchaseOrder.objects.filter(
            source=source, external_number=external_number,
        ).exists()
        if exists:
            raise PoValidationError(
                f'PO already imported for source={source} '
                f'external_number={external_number}.',
            )

    line_rows = _build_line_rows(supplier.id, lines)
    po = PurchaseOrder.objects.create(
        supplier=supplier,
        ship_to_location=ship_to,
        status=status,
        ordered_at=_parse_optional_date(ordered_at, 'ordered_at')
        or (date.today() if status == PurchaseOrderStatus.ORDERED else None),
        expected_at=_parse_optional_date(expected_at, 'expected_at'),
        remarks=remarks,
        source=source,
        external_number=external_number,
        created_by_user_id=created_by_user_id,
    )
    assign_po_number(po)
    PurchaseOrderLine.objects.bulk_create([
        PurchaseOrderLine(purchase_order=po, **row) for row in line_rows
    ])
    return get_purchase_order(po.id)


@transaction.atomic
def update_purchase_order(po_id: int, *, body: dict) -> PurchaseOrder:
    try:
        po = PurchaseOrder.objects.select_for_update().get(pk=po_id)
    except PurchaseOrder.DoesNotExist as exc:
        raise PoValidationError('Purchase order not found.') from exc

    if po.status != PurchaseOrderStatus.DRAFT:
        raise PoValidationError(
            f'Purchase order status={po.status} cannot be edited.',
        )

    new_status = body.get('status')
    marking_ordered = new_status == PurchaseOrderStatus.ORDERED

    if new_status not in (None, '', PurchaseOrderStatus.DRAFT, PurchaseOrderStatus.ORDERED):
        raise PoValidationError(
            'Only draft → ordered status change is allowed here.',
        )

    if 'supplier_id' in body:
        supplier = _require_supplier(int(body['supplier_id']))
        po.supplier = supplier
    if 'ship_to_location_id' in body:
        po.ship_to_location = _optional_location(
            body.get('ship_to_location_id'), 'ship_to_location_id',
        )
    if 'expected_at' in body:
        po.expected_at = _parse_optional_date(body.get('expected_at'), 'expected_at')
    if 'ordered_at' in body:
        po.ordered_at = _parse_optional_date(body.get('ordered_at'), 'ordered_at')
    if 'remarks' in body:
        po.remarks = body.get('remarks') or None

    if 'lines' in body:
        line_rows = _build_line_rows(po.supplier_id, body['lines'])
        po.lines.all().delete()
        PurchaseOrderLine.objects.bulk_create([
            PurchaseOrderLine(purchase_order=po, **row) for row in line_rows
        ])

    if marking_ordered:
        po.status = PurchaseOrderStatus.ORDERED
        if po.ordered_at is None:
            po.ordered_at = date.today()

    po.save()
    return get_purchase_order(po.id)


def get_purchase_order(po_id: int) -> PurchaseOrder:
    return (
        PurchaseOrder.objects.select_related(
            'supplier', 'ship_to_location',
        )
        .prefetch_related(
            Prefetch(
                'lines',
                queryset=PurchaseOrderLine.objects.select_related(
                    'product', 'unit', 'product_supplier',
                ).order_by('line_no'),
            ),
        )
        .get(pk=po_id)
    )


def list_purchase_orders(*, status=None, supplier_id=None):
    qs = PurchaseOrder.objects.select_related(
        'supplier', 'ship_to_location',
    ).order_by('-id')
    if status not in (None, ''):
        qs = qs.filter(status=status)
    if supplier_id not in (None, ''):
        qs = qs.filter(supplier_id=int(supplier_id))
    return qs
