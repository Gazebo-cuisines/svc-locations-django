from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import PurchaseShapeFormat, Unit
from product.query import active_products
from recipe.models import RecipeVersion
from stock_ledger.models import StockLot, StockLotOrigin, StockUnitConversion
from stock_ledger.util import services
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.parse import parse_date as _parse_date, parse_decimal as _parse_decimal
from stock_ledger.util.payloads import lot_dict, unit_conversion_dict
from stock_ledger.views._common import _parse_json_body


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def lots_collection_api(request):
    if request.method == 'GET':
        qs = StockLot.objects.filter(product__is_active=True).order_by('-id')
        product_id = request.GET.get('product_id')
        if product_id not in (None, ''):
            try:
                qs = qs.filter(product_id=int(product_id))
            except (TypeError, ValueError):
                return api_error('product_id must be an integer.')
        return api_success('Lots fetched.', [lot_dict(lot) for lot in qs[:500]])

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')

    required = ['product_id', 'origin']
    missing = [key for key in required if body.get(key) in (None, '')]
    if missing:
        return api_error(f'Missing required field: {missing[0]}')

    origin = body['origin']
    if origin not in StockLotOrigin.values:
        return api_error(
            f'Invalid origin. Use one of: {", ".join(StockLotOrigin.values)}',
        )

    product_id = body['product_id']
    if not active_products().filter(pk=product_id).exists():
        return api_error(f'product_id={product_id} not found.', status_code=404)

    recipe_version_id = body.get('recipe_version_id')
    if (
        recipe_version_id not in (None, '')
        and not RecipeVersion.objects.filter(pk=recipe_version_id).exists()
    ):
        return api_error(
            f'recipe_version_id={recipe_version_id} not found.',
            status_code=400,
        )

    shape_format_id = body.get('shape_format_id')
    if (
        shape_format_id not in (None, '')
        and not PurchaseShapeFormat.objects.filter(pk=shape_format_id).exists()
    ):
        return api_error(
            f'shape_format_id={shape_format_id} not found.',
            status_code=400,
        )

    try:
        production_date = _parse_date(body.get('production_date'), 'production_date')
        use_by = _parse_date(body.get('use_by'), 'use_by')
        trace_date = _parse_date(body.get('trace_date'), 'trace_date')
        trace_number = body.get('trace_number')
        if trace_number in (None, '') and production_date is None and trace_date is not None:
            production_date = trace_date

        lot = services.resolve_lot(
            product_id=product_id,
            trace_number=trace_number or None,
            use_by=use_by,
            production_date=production_date,
            recipe_version_id=recipe_version_id or None,
            shape_format_id=shape_format_id or None,
            origin=origin,
            supplier_lot_code=body.get('supplier_lot_code') or None,
        )
    except ValueError as exc:
        return api_error(str(exc))
    except StockValidationError as exc:
        return api_error(str(exc))

    return api_success('Lot created.', lot_dict(lot), status_code=201)


@csrf_exempt
@require_GET
def lot_detail_api(request, pk: int):
    try:
        lot = StockLot.objects.get(pk=pk)
    except StockLot.DoesNotExist:
        return api_error('Lot not found.', status_code=404)
    return api_success('Lot fetched.', lot_dict(lot))


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def unit_conversions_api(request):
    if request.method == 'GET':
        qs = StockUnitConversion.objects.all().order_by('unit_id', 'product_id')
        product_id = request.GET.get('product_id')
        unit_id = request.GET.get('unit_id')
        if product_id not in (None, ''):
            try:
                qs = qs.filter(product_id=int(product_id))
            except (TypeError, ValueError):
                return api_error('product_id must be an integer.')
        if unit_id not in (None, ''):
            try:
                qs = qs.filter(unit_id=int(unit_id))
            except (TypeError, ValueError):
                return api_error('unit_id must be an integer.')
        return api_success(
            'Unit conversions fetched.',
            [unit_conversion_dict(row) for row in qs[:500]],
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')

    required = ['unit_id', 'to_kg']
    missing = [key for key in required if body.get(key) in (None, '')]
    if missing:
        return api_error(f'Missing required field: {missing[0]}')

    unit_id = body['unit_id']
    if not Unit.objects.filter(pk=unit_id).exists():
        return api_error(f'unit_id={unit_id} not found.', status_code=404)

    product_id = body.get('product_id')
    if product_id not in (None, ''):
        if not active_products().filter(pk=product_id).exists():
            return api_error(f'product_id={product_id} not found.', status_code=404)
    else:
        product_id = None

    try:
        to_kg = _parse_decimal(body['to_kg'], 'to_kg')
        if to_kg <= 0:
            return api_error('to_kg must be greater than 0.')
    except ValueError as exc:
        return api_error(str(exc))

    row, _created = StockUnitConversion.objects.update_or_create(
        unit_id=unit_id,
        product_id=product_id,
        defaults={
            'to_kg': to_kg,
            'source': body.get('source') or 'manual',
        },
    )
    return api_success('Unit conversion saved.', unit_conversion_dict(row), status_code=201)
