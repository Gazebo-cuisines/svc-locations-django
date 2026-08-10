import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.models import Location
from locations.utils.api_response import api_error, api_success
from product.audit_log import capture_product_audit
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductCosting,
    ProductLabelMode,
    PurchaseShapeFormat,
    Unit,
)
from product.query import active_products
from recipe.models import RecipeVersion
from recipe.utils import active_or_latest_version


def _product_qs():
    return Product.objects.select_related(
        'category', 'unit', 'shelf_life', 'recipe', 'costing',
    ).prefetch_related(
        Prefetch(
            'recipe__versions',
            queryset=RecipeVersion.objects.order_by('-version_number'),
        ),
    )


def _live_recipe_version_id(product: Product) -> int | None:
    try:
        recipe = product.recipe
    except ObjectDoesNotExist:
        return None
    version = active_or_latest_version(recipe)
    return version.id if version is not None else None


def product_list_dict(product: Product) -> dict:
    try:
        shelf_life_days = product.shelf_life.shelf_life_days
    except ObjectDoesNotExist:
        shelf_life_days = None
    return {
        'id': product.id,
        'name': product.name,
        'alternate_name': product.alternate_name,
        'recipe_code': product.recipe_code,
        'is_active': product.is_active,
        'product_class_id': product.product_class_id,
        'category_id': product.category_id,
        'category_path': product.category.path if product.category_id else None,
        'unit_id': product.unit_id,
        'unit_name': product.unit.name,
        'source_container_id': product.source_container_id,
        'destination_container_id': product.destination_container_id,
        'shelf_life_days': shelf_life_days,
        'recipe_version_id': _live_recipe_version_id(product),
    }


def product_detail_dict(product: Product) -> dict:
    data = product_list_dict(product)
    try:
        costing = product.costing
        costing_data = {
            'nominal_code': costing.nominal_code,
            'price': str(costing.unit_price),
        }
    except ProductCosting.DoesNotExist:
        costing_data = None
    data.update({
        'alternate_recipe_code': product.alternate_recipe_code,
        'gff_code': product.gff_code,
        'secondary_gff_recipe': product.secondary_gff_recipe,
        'external_barcode': product.external_barcode,
        'label_mode': product.label_mode,
        'is_downtime': product.is_downtime,
        'ingredient_count': product.ingredient_count,
        'remarks': product.remarks,
        'purchase_details': {
            'purchase_unit_id': product.purchasing_unit_id,
            'purchase_format': product.purchase_shape_format_id,
            'purchase_version': product.purchasing_version,
        },
        'costing': costing_data,
        'created_at': product.created_at.isoformat() if product.created_at else None,
        'updated_at': product.updated_at.isoformat() if product.updated_at else None,
    })
    return data


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _label_mode_error(value) -> str | None:
    if value in ProductLabelMode.values:
        return None
    return (
        f'Invalid label_mode. Use one of: {", ".join(ProductLabelMode.values)}.'
    )


def _parse_costing_decimal(value, field_name: str) -> Decimal:
    if value is None or value == '':
        return Decimal('0')
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f'Invalid decimal for costing.{field_name}.') from exc


def _apply_nested_costing(product: Product, body: dict):
    """Optional nested costing: { nominal_code, price } — price maps to unit_price."""
    costing = body.get('costing')
    if not isinstance(costing, dict):
        return
    nominal = costing.get('nominal_code')
    if nominal in ('',):
        nominal = None
    if nominal is not None:
        nominal = str(nominal)[:4] or None
    price = costing.get('price', costing.get('unit_price', 0))
    unit_cost = costing.get('unit_cost', 0)
    ProductCosting.objects.update_or_create(
        product_id=product.id,
        defaults={
            'nominal_code': nominal,
            'unit_price': _parse_costing_decimal(price, 'price'),
            'unit_cost': _parse_costing_decimal(unit_cost, 'unit_cost'),
        },
    )


def _purchase_details_from_body(body: dict) -> dict | None:
    """Return purchase payload keys to apply, or None if none provided."""
    nested = body.get('purchase_details')
    if isinstance(nested, dict):
        payload = {}
        if 'purchase_unit_id' in nested:
            payload['purchase_unit_id'] = nested.get('purchase_unit_id')
        if 'purchase_format' in nested:
            payload['purchase_format'] = nested.get('purchase_format')
        if 'purchase_version' in nested:
            payload['purchase_version'] = nested.get('purchase_version')
        return payload or None

    payload = {}
    if 'purchase_unit_id' in body or 'purchasing_unit_id' in body:
        payload['purchase_unit_id'] = body.get(
            'purchase_unit_id', body.get('purchasing_unit_id'),
        )
    if 'purchase_format' in body or 'purchase_shape_format_id' in body:
        payload['purchase_format'] = body.get(
            'purchase_format', body.get('purchase_shape_format_id'),
        )
    if 'purchase_version' in body or 'purchasing_version' in body:
        payload['purchase_version'] = body.get(
            'purchase_version', body.get('purchasing_version'),
        )
    return payload or None


def _apply_purchase_details(product: Product, purchase: dict):
    if 'purchase_unit_id' in purchase:
        unit_id = purchase['purchase_unit_id']
        if unit_id is None:
            product.purchasing_unit_id = None
        elif not Unit.objects.filter(pk=unit_id).exists():
            raise ValueError(f'purchase_unit_id={unit_id} not found.')
        else:
            product.purchasing_unit_id = unit_id

    if 'purchase_format' in purchase:
        format_id = purchase['purchase_format']
        if format_id is None:
            product.purchase_shape_format_id = None
        elif not PurchaseShapeFormat.objects.filter(pk=format_id).exists():
            raise ValueError(f'purchase_format={format_id} not found.')
        else:
            product.purchase_shape_format_id = format_id

    if 'purchase_version' in purchase:
        product.purchasing_version = purchase.get('purchase_version')


def _parse_optional_int(raw, field_name: str):
    if raw is None or raw == '':
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} must be an integer.')


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_collection_api(request):
    if request.method == 'GET':
        try:
            source_id = _parse_optional_int(
                request.GET.get('source_container_id'), 'source_container_id',
            )
            dest_id = _parse_optional_int(
                request.GET.get('destination_container_id'),
                'destination_container_id',
            )
        except ValueError as exc:
            return api_error(str(exc), status_code=400)

        products = active_products(
            source_container_id=source_id,
            destination_container_id=dest_id,
        )
        return api_success(
            'Product list fetched successfully.',
            [product_list_dict(p) for p in products],
        )
    return product_create_api(request)


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def product_detail_api(request, pk: int):
    try:
        # GET by pk includes delisted rows so unit/lookup blockers remain openable.
        product = _product_qs().get(pk=pk)
    except Product.DoesNotExist:
        return api_error('Product not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Product fetched successfully.', product_detail_dict(product))
    if request.method == 'DELETE':
        before_data = product_detail_dict(product)
        product.is_active = False
        product.save(update_fields=['is_active', 'updated_at'])
        after_data = product_detail_dict(product)
        capture_product_audit(
            request,
            product_id=product.id,
            entity='product',
            action='delete',
            before_data=before_data,
            after_data=after_data,
        )
        return api_success(
            'Product deactivated successfully.',
            after_data,
        )
    return product_update_api(request, product)


def product_update_api(request, product: Product):
    before_data = product_detail_dict(product)
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    lookup_checks = (
        ('product_class_id', ProductClass),
        ('category_id', Category),
        ('unit_id', Unit),
        ('source_container_id', Location),
        ('destination_container_id', Location),
    )
    for field, model in lookup_checks:
        if field not in body:
            continue
        value = body[field]
        if value is None:
            return api_error(f'{field} cannot be null.', status_code=400)
        if not model.objects.filter(pk=value).exists():
            return api_error(f'{field}={value} not found.', status_code=400)
        setattr(product, field, value)

    if 'label_mode' in body:
        error = _label_mode_error(body['label_mode'])
        if error is not None:
            return api_error(error, status_code=400)

    for field in (
        'name',
        'alternate_name',
        'recipe_code',
        'alternate_recipe_code',
        'gff_code',
        'secondary_gff_recipe',
        'external_barcode',
        'label_mode',
        'is_active',
        'is_downtime',
        'ingredient_count',
        'remarks',
    ):
        if field in body:
            setattr(product, field, body[field])

    purchase = _purchase_details_from_body(body)
    if purchase is not None:
        try:
            _apply_purchase_details(product, purchase)
        except ValueError as exc:
            return api_error(str(exc), status_code=400)

    try:
        with transaction.atomic():
            product.save()
            _apply_nested_costing(product, body)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not update product: {exc}', status_code=400)

    product = _product_qs().get(pk=product.pk)
    after_data = product_detail_dict(product)
    capture_product_audit(
        request,
        product_id=product.id,
        entity='product',
        action='update',
        before_data=before_data,
        after_data=after_data,
    )
    return api_success(
        'Product updated successfully.',
        after_data,
    )


def product_create_api(request):
    """Create a core product row. Required FKs must already exist."""
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    required = [
        'name',
        'product_class_id',
        'category_id',
        'unit_id',
        'source_container_id',
        'destination_container_id',
    ]
    missing = [key for key in required if body.get(key) in (None, '')]
    if missing:
        return api_error(
            f'Missing required fields: {", ".join(missing)}',
            status_code=400,
        )

    try:
        ProductClass.objects.get(pk=body['product_class_id'])
        Category.objects.get(pk=body['category_id'])
        Unit.objects.get(pk=body['unit_id'])
    except (
        ProductClass.DoesNotExist,
        Category.DoesNotExist,
        Unit.DoesNotExist,
    ) as exc:
        return api_error(f'Invalid lookup reference: {exc}', status_code=400)

    for field in ('source_container_id', 'destination_container_id'):
        if not Location.objects.filter(pk=body[field]).exists():
            return api_error(f'{field}={body[field]} not found.', status_code=400)

    label_mode = body.get('label_mode', ProductLabelMode.PRODUCT)
    label_mode_error = _label_mode_error(label_mode)
    if label_mode_error is not None:
        return api_error(label_mode_error, status_code=400)

    purchase = _purchase_details_from_body(body) or {}
    try:
        with transaction.atomic():
            product = Product(
                name=body['name'],
                alternate_name=body.get('alternate_name'),
                recipe_code=body.get('recipe_code'),
                alternate_recipe_code=body.get('alternate_recipe_code'),
                gff_code=body.get('gff_code'),
                secondary_gff_recipe=body.get('secondary_gff_recipe'),
                external_barcode=body.get('external_barcode'),
                label_mode=label_mode,
                is_active=body.get('is_active', True),
                is_downtime=body.get('is_downtime', False),
                ingredient_count=body.get('ingredient_count'),
                remarks=body.get('remarks'),
                product_class_id=body['product_class_id'],
                category_id=body['category_id'],
                unit_id=body['unit_id'],
                source_container_id=body['source_container_id'],
                destination_container_id=body['destination_container_id'],
            )
            _apply_purchase_details(product, purchase)
            product.save()
            _apply_nested_costing(product, body)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not create product: {exc}', status_code=400)

    product = _product_qs().get(pk=product.pk)
    after_data = product_detail_dict(product)
    capture_product_audit(
        request,
        product_id=product.id,
        entity='product',
        action='create',
        before_data=None,
        after_data=after_data,
    )
    return api_success(
        'Product created successfully.',
        {'ref': product.id, **after_data},
        status_code=201,
    )
