import json
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.models import Location, LocationRole
from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.query import active_products
from product.models import (
    Category,
    Product,
    ProductSupplier,
    PurchaseShapeFormat,
    Unit,
)

_PURCHASE_CATEGORY_NAMES = ('Raw Materials', 'Packaging Material')


def _dec(value) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def _cost_per_unit(cost, multiplier) -> str | None:
    if cost is None:
        return None
    return _dec((cost / multiplier).quantize(Decimal('0.000001')))


def supplier_product_dict(row: ProductSupplier) -> dict:
    return {
        'id': row.id,
        'product_id': row.product_id,
        'product_name': row.product.name,
        'supplier_id': row.supplier_id,
        'supplier_name': row.supplier.name,
        'supplier_code': row.supplier_code,
        'sage_product_code': row.sage_product_code,
        'supplier_product_name': row.supplier_product_name,
        'cost': _dec(row.cost),
        # cost / multiplier — unit is pack inner (e.g. Kg), not product.unit
        'cost_per_unit': _cost_per_unit(row.cost, row.multiplier),
        'cost_unit_name': row.inner_unit.name,
        'moq': row.moq,
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


def _report_qs():
    return _base_qs().select_related(
        'product__product_class',
        'product__category',
    )


def _category_subtree_ids(root_ids: list[int]) -> list[int]:
    """Root ids plus all descendant category ids (by parent)."""
    if not root_ids:
        return []
    children_by_parent: dict[int | None, list[int]] = {}
    for cid, parent_id in Category.objects.values_list('id', 'parent_id'):
        children_by_parent.setdefault(parent_id, []).append(cid)

    out: list[int] = []
    stack = list(root_ids)
    seen: set[int] = set()
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
        stack.extend(children_by_parent.get(cid, []))
    return out


def _purchase_category_ids(category_id: int | None = None) -> list[int]:
    roots = list(
        Category.objects.filter(name__in=_PURCHASE_CATEGORY_NAMES).values_list(
            'id', flat=True,
        )
    )
    allowed = set(_category_subtree_ids(roots))
    if category_id is None:
        return list(allowed)
    if category_id not in allowed:
        return []
    return _category_subtree_ids([category_id])


def purchase_costing_row_dict(row: ProductSupplier) -> dict:
    data = supplier_product_dict(row)
    product = row.product
    category = product.category
    data.update({
        'goods_in_type': product.goods_in_type,
        'product_class_id': product.product_class_id,
        'product_class_name': product.product_class.name,
        'category_id': product.category_id,
        'category_name': category.name if category else None,
        'recipe_code': product.recipe_code,
        'alternate_recipe_code': product.alternate_recipe_code,
        'base_unit_id': product.unit_id,
    })
    return data


def _supplier_qs(product_id: int):
    return _base_qs().filter(product_id=product_id)


def _parse_optional_int(raw, field_name: str):
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'Invalid integer for {field_name}.') from exc


def _parse_optional_bool(raw, field_name: str):
    if raw in (None, ''):
        return None
    value = str(raw).strip().lower()
    if value in ('1', 'true', 'yes'):
        return True
    if value in ('0', 'false', 'no'):
        return False
    raise ValueError(f'Invalid boolean for {field_name}.')


@require_GET
def purchase_costing_report_api(request):
    try:
        category_id = _parse_optional_int(request.GET.get('category_id'), 'category_id')
        supplier_id = _parse_optional_int(request.GET.get('supplier_id'), 'supplier_id')
        product_id = _parse_optional_int(request.GET.get('product_id'), 'product_id')
        is_default = _parse_optional_bool(request.GET.get('is_default'), 'is_default')
        is_active = _parse_optional_bool(request.GET.get('is_active'), 'is_active')
        has_cost = _parse_optional_bool(request.GET.get('has_cost'), 'has_cost')
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    category_ids = _purchase_category_ids(category_id)
    if category_id is not None and not category_ids:
        return api_error(
            'category_id must be Raw Materials, Packaging Material, or a child of those.',
            status_code=400,
        )

    qs = _report_qs().filter(
        product__is_active=True,
        product__category_id__in=category_ids,
    )
    if supplier_id is not None:
        qs = qs.filter(supplier_id=supplier_id)
    if product_id is not None:
        qs = qs.filter(product_id=product_id)
    if is_default is not None:
        qs = qs.filter(is_default=is_default)
    if is_active is None or is_active:
        qs = qs.filter(is_active=True)
    else:
        qs = qs.filter(is_active=False)
    if has_cost is True:
        qs = qs.filter(cost__isnull=False)
    elif has_cost is False:
        qs = qs.filter(cost__isnull=True)

    rows = qs.order_by(
        'product__name', 'supplier__name', 'supplier_code',
    )
    return api_success(
        'Purchase costing report fetched successfully.',
        [purchase_costing_row_dict(row) for row in rows],
    )


@require_GET
def supplier_products_list_api(request):
    try:
        supplier_id = _parse_optional_int(request.GET.get('supplier_id'), 'supplier_id')
        product_id = _parse_optional_int(request.GET.get('product_id'), 'product_id')
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    qs = _base_qs().filter(product__is_active=True)
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

    if 'sage_product_code' in body:
        sage = (body.get('sage_product_code') or '').strip() or None
        data['sage_product_code'] = sage

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

    if 'moq' in body:
        moq = _parse_optional_int(body.get('moq'), 'moq')
        if moq is not None and moq <= 0:
            raise ValueError('moq must be greater than 0.')
        data['moq'] = moq

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
    if not active_products().filter(pk=pk).exists():
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
