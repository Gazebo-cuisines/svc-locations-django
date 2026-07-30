import json
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.models import Location, LocationRole
from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import Product, ProductSupplier, PurchaseShapeFormat, Unit


def _dec(value) -> str | None:
    if value is None:
        return None
    return str(value)


def supplier_product_dict(row: ProductSupplier) -> dict:
    return {
        'id': row.id,
        'product_id': row.product_id,
        'product_name': row.product.name,
        'supplier_id': row.supplier_id,
        'supplier_name': row.supplier.name,
        'supplier_code': row.supplier_code,
        'supplier_product_name': row.supplier_product_name,
        'cost': _dec(row.cost),
        'outer_qty': _dec(row.outer_qty),
        'outer_unit_id': row.outer_unit_id,
        'outer_unit_name': row.outer_unit.name,
        'inner_qty': _dec(row.inner_qty),
        'inner_unit_id': row.inner_unit_id,
        'inner_unit_name': row.inner_unit.name,
        'multiplier': _dec(row.multiplier),
        'shape_format_label': row.shape_format_label,
        'base_unit_name': row.product.unit.name,
        'purchase_shape_format_id': row.purchase_shape_format_id,
        'is_default': row.is_default,
        'is_active': row.is_active,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def _base_qs():
    return ProductSupplier.objects.select_related(
        'supplier',
        'outer_unit',
        'inner_unit',
        'product',
        'product__unit',
        'purchase_shape_format',
    )


def _supplier_qs(product_id: int):
    return _base_qs().filter(product_id=product_id)


def _parse_optional_int(raw, field_name: str):
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid integer for {field_name}.') from exc


@require_GET
def supplier_products_list_api(request):
    try:
        supplier_id = _parse_optional_int(request.GET.get('supplier_id'), 'supplier_id')
        product_id = _parse_optional_int(request.GET.get('product_id'), 'product_id')
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    qs = _base_qs()
    if supplier_id is not None:
        qs = qs.filter(supplier_id=supplier_id)
    if product_id is not None:
        qs = qs.filter(product_id=product_id)

    rows = qs.order_by('supplier__name', 'product__name', 'supplier_code')
    return api_success(
        'Supplier products fetched successfully.',
        [supplier_product_dict(row) for row in rows],
    )


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_decimal(value, field_name: str):
    if value is None or value == '':
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid decimal for {field_name}.') from exc


def _is_supplier(location_id: int) -> bool:
    return Location.objects.filter(
        pk=location_id,
        roles__role=LocationRole.SUPPLIER,
    ).exists()


def _require_unit(unit_id, field_name: str) -> int:
    if unit_id in (None, ''):
        raise ValueError(f'{field_name} is required.')
    if not Unit.objects.filter(pk=unit_id).exists():
        raise ValueError(f'{field_name}={unit_id} not found.')
    return unit_id


def _require_positive_decimal(value, field_name: str) -> Decimal:
    parsed = _parse_decimal(value, field_name)
    if parsed is None or parsed <= 0:
        raise ValueError(f'{field_name} must be greater than 0.')
    return parsed


def _validate_write_fields(body: dict, *, partial: bool = False) -> dict:
    """Return cleaned fields to apply. Raises ValueError on bad input."""
    data = {}

    if 'supplier_id' in body or not partial:
        if 'supplier_id' not in body and not partial:
            raise ValueError('Missing required fields: supplier_id')
        if 'supplier_id' in body:
            supplier_id = body.get('supplier_id')
            if supplier_id in (None, ''):
                raise ValueError('supplier_id is required.')
            if not _is_supplier(supplier_id):
                raise ValueError(f'supplier_id={supplier_id} is not a supplier.')
            data['supplier_id'] = supplier_id

    if 'supplier_code' in body or not partial:
        if 'supplier_code' not in body and not partial:
            raise ValueError('Missing required fields: supplier_code')
        if 'supplier_code' in body:
            code = (body.get('supplier_code') or '').strip()
            if not code:
                raise ValueError('supplier_code cannot be empty.')
            data['supplier_code'] = code

    if 'supplier_product_name' in body or not partial:
        if 'supplier_product_name' not in body and not partial:
            raise ValueError('Missing required fields: supplier_product_name')
        if 'supplier_product_name' in body:
            name = (body.get('supplier_product_name') or '').strip()
            if not name:
                raise ValueError('supplier_product_name cannot be empty.')
            data['supplier_product_name'] = name

    shape_required = ('outer_qty', 'outer_unit_id', 'inner_qty', 'inner_unit_id')
    if not partial:
        missing = [key for key in shape_required if key not in body]
        if missing:
            raise ValueError(f'Missing required fields: {", ".join(missing)}')

    if 'outer_qty' in body:
        data['outer_qty'] = _require_positive_decimal(body.get('outer_qty'), 'outer_qty')
    if 'outer_unit_id' in body:
        data['outer_unit_id'] = _require_unit(body.get('outer_unit_id'), 'outer_unit_id')
    if 'inner_qty' in body:
        data['inner_qty'] = _require_positive_decimal(body.get('inner_qty'), 'inner_qty')
    if 'inner_unit_id' in body:
        data['inner_unit_id'] = _require_unit(body.get('inner_unit_id'), 'inner_unit_id')

    if 'cost' in body:
        cost = _parse_decimal(body.get('cost'), 'cost')
        if cost is not None and cost < 0:
            raise ValueError('cost cannot be negative.')
        data['cost'] = cost

    if 'purchase_shape_format_id' in body:
        format_id = body.get('purchase_shape_format_id')
        if format_id in (None, ''):
            data['purchase_shape_format_id'] = None
        elif not PurchaseShapeFormat.objects.filter(pk=format_id).exists():
            raise ValueError(f'purchase_shape_format_id={format_id} not found.')
        else:
            data['purchase_shape_format_id'] = format_id

    if 'is_default' in body:
        data['is_default'] = bool(body['is_default'])
    if 'is_active' in body:
        data['is_active'] = bool(body['is_active'])

    return data


def _clear_other_defaults(product_id: int, keep_id: int | None = None):
    qs = ProductSupplier.objects.filter(product_id=product_id, is_default=True)
    if keep_id is not None:
        qs = qs.exclude(pk=keep_id)
    qs.update(is_default=False)


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_suppliers_api(request, pk: int):
    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        rows = _supplier_qs(pk).order_by('-is_default', 'supplier__name', 'supplier_code')
        return api_success(
            'Product suppliers fetched successfully.',
            [supplier_product_dict(row) for row in rows],
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        data = _validate_write_fields(body, partial=False)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    try:
        with transaction.atomic():
            if data.get('is_default'):
                _clear_other_defaults(pk)
            row = ProductSupplier(product_id=pk, **data)
            row.save()
    except IntegrityError:
        return api_error(
            'Supplier product with this supplier_code already exists for this supplier.',
            status_code=409,
        )

    row = _supplier_qs(pk).get(pk=row.id)
    after_data = supplier_product_dict(row)
    capture_product_audit(
        request,
        product_id=pk,
        entity='supplier_product',
        action='create',
        before_data=None,
        after_data=after_data,
    )
    return api_success(
        'Supplier product created successfully.',
        after_data,
        status_code=201,
    )


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def product_supplier_detail_api(request, pk: int, row_id: int):
    try:
        row = _supplier_qs(pk).get(pk=row_id)
    except ProductSupplier.DoesNotExist:
        return api_error('Supplier product not found.', status_code=404)

    if request.method == 'GET':
        return api_success(
            'Supplier product fetched successfully.',
            supplier_product_dict(row),
        )

    if request.method == 'DELETE':
        before_data = supplier_product_dict(row)
        row.delete()
        capture_product_audit(
            request,
            product_id=pk,
            entity='supplier_product',
            action='delete',
            before_data=before_data,
            after_data=None,
        )
        return api_success('Supplier product deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        data = _validate_write_fields(body, partial=True)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    if not data:
        return api_error('No fields to update.', status_code=400)

    before_data = supplier_product_dict(row)
    try:
        with transaction.atomic():
            if data.get('is_default'):
                _clear_other_defaults(pk, keep_id=row.id)
            for field, value in data.items():
                setattr(row, field, value)
            row.save()
    except IntegrityError:
        return api_error(
            'Supplier product with this supplier_code already exists for this supplier.',
            status_code=409,
        )

    row = _supplier_qs(pk).get(pk=row.id)
    after_data = supplier_product_dict(row)
    capture_product_audit(
        request,
        product_id=pk,
        entity='supplier_product',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success('Supplier product updated successfully.', after_data)
