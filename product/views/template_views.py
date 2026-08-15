from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import (
    Product,
    ProductCosting,
    ProductFlags,
    ProductPackaging,
    ProductProduction,
    ProductShelfLife,
    Unit,
)
from product.views.costing_views import costing_dict
from product.views.flags_views import flags_dict
from product.views.packaging_views import packaging_dict
from product.views.product_master_view import (
    _create_core_product,
    _parse_json_body,
    _product_qs,
    product_detail_dict,
)
from product.views.production_views import production_dict
from product.views.shelf_life_views import shelf_life_dict


def _fmt_qty(value: Decimal) -> str:
    text = format(value.normalize(), 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


def derive_case_size(
    items_per_unit: Decimal,
    unitary_weight: Decimal,
    unit_name: str,
) -> tuple[Decimal, str]:
    pack_weight = items_per_unit * unitary_weight
    label = (
        f'{_fmt_qty(items_per_unit)} x {_fmt_qty(unitary_weight)} '
        f'{(unit_name or "").strip().upper()}'
    )
    return pack_weight, label


def _positive_decimal(body: dict, field: str) -> Decimal:
    raw = body.get(field)
    if raw in (None, ''):
        raise ValueError(f'Missing required fields: {field}')
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid decimal for {field}.') from exc
    if value <= 0:
        raise ValueError(f'{field} must be greater than 0.')
    return value


def _require_int(body: dict, field: str) -> int:
    raw = body.get(field)
    if raw in (None, ''):
        raise ValueError(f'Missing required fields: {field}')
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field} must be an integer.') from exc


def _optional_int(body: dict, field: str) -> int | None:
    if field not in body or body.get(field) in (None, ''):
        return None
    return _require_int(body, field)


def _optional_decimal(body: dict, field: str) -> Decimal | None:
    if field not in body or body.get(field) in (None, ''):
        return None
    try:
        return Decimal(str(body[field]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid decimal for {field}.') from exc


def _template_response_data(
    product: Product,
    packaging: ProductPackaging,
    shelf_life: ProductShelfLife,
    flags: ProductFlags,
    production: ProductProduction,
    costing: ProductCosting,
) -> dict:
    return {
        'ref': product.id,
        **product_detail_dict(product),
        'packaging': packaging_dict(packaging),
        'shelf_life': shelf_life_dict(shelf_life),
        'flags': flags_dict(flags),
        'production': production_dict(production),
        'costing': costing_dict(costing),
    }


def _parse_sleeving_extras(body: dict) -> dict:
    packed_id = _require_int(body, 'packed_product_id')
    if not Product.objects.filter(pk=packed_id).exists():
        raise ValueError(f'packed_product_id={packed_id} not found.')

    items_per_unit = _positive_decimal(body, 'items_per_unit')
    unitary_weight = _positive_decimal(body, 'unitary_weight')
    unit_id = _require_int(body, 'case_size_unit_id')
    try:
        unit = Unit.objects.get(pk=unit_id)
    except Unit.DoesNotExist as exc:
        raise ValueError(f'case_size_unit_id={unit_id} not found.') from exc

    pack_weight, case_size = derive_case_size(
        items_per_unit, unitary_weight, unit.name,
    )
    return {
        'items_per_unit': items_per_unit,
        'unitary_weight': unitary_weight,
        'pack_weight': pack_weight,
        'case_size': case_size,
        'shelf_life_days': _require_int(body, 'shelf_life_days'),
        'depot_days': _optional_int(body, 'shelf_life_depot_days'),
        'force_trace_number': (
            bool(body['force_trace_number'])
            if 'force_trace_number' in body
            else False
        ),
        'default_resource_id': _optional_int(body, 'default_resource_id'),
    }


def _create_sleeving_satellites(product: Product, extras: dict) -> dict:
    packaging = ProductPackaging.objects.create(
        product=product,
        items_per_unit=extras['items_per_unit'],
        unitary_weight=extras['unitary_weight'],
        pack_weight=extras['pack_weight'],
    )
    shelf_life = ProductShelfLife.objects.create(
        product=product,
        shelf_life_days=extras['shelf_life_days'],
        shelf_life_depot_days=extras['depot_days'],
        force_production_date=True,
        force_use_by=True,
        force_trace_number=extras['force_trace_number'],
    )
    flags = ProductFlags.objects.create(
        product=product,
        is_sales_item=True,
        has_plan=True,
        include_in_projections=True,
    )
    production = ProductProduction.objects.create(
        product=product,
        default_resource_id=extras['default_resource_id'],
    )
    costing, _ = ProductCosting.objects.update_or_create(
        product_id=product.id,
        defaults={'case_size_description': extras['case_size']},
    )
    return _template_response_data(
        product, packaging, shelf_life, flags, production, costing,
    )


def _parse_high_risk_extras(body: dict) -> dict:
    gas_raw = body.get('is_gas_flush')
    if 'is_gas_flush' not in body or gas_raw in (None, ''):
        is_gas_flush = True
    else:
        is_gas_flush = bool(gas_raw)
    return {
        'pack_weight': _positive_decimal(body, 'pack_weight'),
        'is_gas_flush': is_gas_flush,
        'shelf_life_days': _require_int(body, 'shelf_life_days'),
        'avg_staff_min_per_unit': _optional_decimal(body, 'avg_staff_min_per_unit'),
        'avg_staff_per_minute': _optional_decimal(body, 'avg_staff_per_minute'),
    }


def _create_high_risk_satellites(product: Product, extras: dict) -> dict:
    packaging = ProductPackaging.objects.create(
        product=product,
        pack_weight=extras['pack_weight'],
        is_gas_flush=extras['is_gas_flush'],
    )
    shelf_life = ProductShelfLife.objects.create(
        product=product,
        shelf_life_days=extras['shelf_life_days'],
    )
    flags = ProductFlags.objects.create(
        product=product,
        is_sales_item=False,
        include_in_projections=False,
    )
    production = ProductProduction.objects.create(
        product=product,
        avg_staff_min_per_unit=extras['avg_staff_min_per_unit'],
        avg_staff_per_minute=extras['avg_staff_per_minute'],
    )
    costing, _ = ProductCosting.objects.get_or_create(product_id=product.id)
    return _template_response_data(
        product, packaging, shelf_life, flags, production, costing,
    )


def _run_template_create(body: dict, parse_extras, write_satellites) -> dict:
    extras = parse_extras(body)
    with transaction.atomic():
        product = _create_core_product(body)
        return write_satellites(_product_qs().get(pk=product.pk), extras)


@require_http_methods(['POST'])
@csrf_exempt
def product_sleeving_create_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        after_data = _run_template_create(
            body, _parse_sleeving_extras, _create_sleeving_satellites,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not create product: {exc}', status_code=400)

    capture_product_audit(
        request,
        product_id=after_data['ref'],
        entity='product',
        action='create',
        before_data=None,
        after_data=after_data,
    )
    return api_success(
        'Product created successfully.',
        after_data,
        status_code=201,
    )


@require_http_methods(['POST'])
@csrf_exempt
def product_high_risk_create_api(request):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        after_data = _run_template_create(
            body, _parse_high_risk_extras, _create_high_risk_satellites,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not create product: {exc}', status_code=400)

    capture_product_audit(
        request,
        product_id=after_data['ref'],
        entity='product',
        action='create',
        before_data=None,
        after_data=after_data,
    )
    return api_success(
        'Product created successfully.',
        after_data,
        status_code=201,
    )
