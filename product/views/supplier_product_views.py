import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import IntegrityError
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


def _cost_per_base(cost: Decimal, conversion: Decimal) -> str | None:
    if conversion is None or conversion <= 0:
        return None
    return str((cost / conversion).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP))


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
        'pack_unit_id': row.pack_unit_id,
        'pack_unit_name': row.pack_unit.name,
        'conversion_to_base': _dec(row.conversion_to_base),
        'base_unit_name': row.product.unit.name,
        'cost_per_base_unit': _cost_per_base(row.cost, row.conversion_to_base),
        'purchase_shape_format_id': row.purchase_shape_format_id,
        'is_active': row.is_active,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def _base_qs():
    return ProductSupplier.objects.select_related(
        'supplier',
        'pack_unit',
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

    if 'pack_unit_id' in body or not partial:
        if 'pack_unit_id' not in body and not partial:
            raise ValueError('Missing required fields: pack_unit_id')
        if 'pack_unit_id' in body:
            pack_unit_id = body.get('pack_unit_id')
            if pack_unit_id in (None, ''):
                raise ValueError('pack_unit_id is required.')
            if not Unit.objects.filter(pk=pack_unit_id).exists():
                raise ValueError(f'pack_unit_id={pack_unit_id} not found.')
            data['pack_unit_id'] = pack_unit_id

    if 'conversion_to_base' in body or not partial:
        if 'conversion_to_base' not in body and not partial:
            raise ValueError('Missing required fields: conversion_to_base')
        if 'conversion_to_base' in body:
            conversion = _parse_decimal(body.get('conversion_to_base'), 'conversion_to_base')
            if conversion is None or conversion <= 0:
                raise ValueError('conversion_to_base must be greater than 0.')
            data['conversion_to_base'] = conversion

    if 'cost' in body:
        cost = _parse_decimal(body.get('cost'), 'cost')
        if cost is None:
            cost = Decimal('0')
        if cost < 0:
            raise ValueError('cost cannot be negative.')
        data['cost'] = cost
    elif not partial:
        data['cost'] = Decimal('0')

    if 'purchase_shape_format_id' in body:
        format_id = body.get('purchase_shape_format_id')
        if format_id in (None, ''):
            data['purchase_shape_format_id'] = None
        elif not PurchaseShapeFormat.objects.filter(pk=format_id).exists():
            raise ValueError(f'purchase_shape_format_id={format_id} not found.')
        else:
            data['purchase_shape_format_id'] = format_id

    if 'is_active' in body:
        data['is_active'] = bool(body['is_active'])

    return data


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_suppliers_api(request, pk: int):
    if not Product.objects.filter(pk=pk).exists():
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        rows = _supplier_qs(pk).order_by('supplier__name', 'supplier_code')
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
        row = ProductSupplier.objects.create(product_id=pk, **data)
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
    for field, value in data.items():
        setattr(row, field, value)

    try:
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
