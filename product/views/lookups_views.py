import json

from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from locations.utils.api_response import api_error, api_success
from product.category_images import category_image_url, upload_category_image
from product.models import (
    AllergenCode,
    Category,
    DeliveryState,
    PackagingType,
    PhysicalState,
    ProductClass,
    PurchaseShapeFormat,
    Range,
    SubRange,
    Unit,
)
from product.query import active_products
from product.views.product_master_view import product_list_dict


def _rows(queryset):
    return [{'id': row.id, 'name': row.name} for row in queryset.order_by('name')]


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _format_dict(row: PurchaseShapeFormat) -> dict:
    return {'id': row.id, 'name': row.name}


@require_GET
def product_class_list_api(request):
    return api_success('Product classes fetched successfully.', _rows(ProductClass.objects.all()))


def _category_dict(row: Category, *, include_children: bool = True) -> dict:
    data = {
        'id': row.id,
        'name': row.name,
        'parent_id': row.parent_id,
        'path': row.path,
        'code_generator': row.code_generator,
        'remarks': row.remarks,
        'image_key': row.image_key,
        'image_url': category_image_url(row),
    }
    if include_children:
        data['children'] = []
    return data


def _category_tree(root_parent_id=None) -> list:
    nodes = {
        row.id: _category_dict(row)
        for row in Category.objects.all().order_by('name')
    }
    roots = []
    for node in nodes.values():
        parent_id = node['parent_id']
        if parent_id in nodes:
            nodes[parent_id]['children'].append(node)
        else:
            roots.append(node)
    if root_parent_id is None:
        return roots
    parent = nodes.get(root_parent_id)
    return parent['children'] if parent else []


def _parse_optional_int(body, key):
    if key not in body:
        return None, False
    value = body.get(key)
    if value in (None, '', 'null'):
        return None, True
    try:
        return int(value), True
    except (TypeError, ValueError):
        return None, False


def _is_descendant(ancestor_id: int, maybe_child_id: int) -> bool:
    """True if maybe_child_id is ancestor_id or under it in the tree."""
    if ancestor_id == maybe_child_id:
        return True
    current = Category.objects.filter(pk=maybe_child_id).values_list('parent_id', flat=True).first()
    seen = set()
    while current is not None:
        if current == ancestor_id:
            return True
        if current in seen:
            break
        seen.add(current)
        current = (
            Category.objects.filter(pk=current).values_list('parent_id', flat=True).first()
        )
    return False


def _derive_category_path(name: str, parent_id) -> str:
    if parent_id is None:
        return name
    parent = Category.objects.filter(pk=parent_id).only('path', 'name').first()
    if not parent:
        return name
    base = (parent.path or parent.name or '').strip()
    return f'{base} / {name}' if base else name


def _apply_category_fields(row: Category, body: dict, *, creating: bool):
    """Apply writable public fields from body. Returns error response or None."""
    name_changed = False
    parent_changed = False

    if 'name' in body or creating:
        name = (body.get('name') or '').strip()
        if not name:
            return api_error('name cannot be empty.', status_code=400)
        row.name = name
        name_changed = True

    if 'parent_id' in body or creating:
        parent_id, ok = _parse_optional_int(body, 'parent_id') if 'parent_id' in body else (None, True)
        if 'parent_id' in body and not ok:
            return api_error('parent_id must be an integer or null.', status_code=400)
        if creating and 'parent_id' not in body:
            parent_id = None
        if parent_id is not None:
            if parent_id == row.id:
                return api_error('Category cannot be its own parent.', status_code=400)
            if not Category.objects.filter(pk=parent_id).exists():
                return api_error(f'Parent category id={parent_id} not found.', status_code=400)
            if row.id and _is_descendant(row.id, parent_id):
                return api_error('Cannot set parent to a descendant category.', status_code=400)
        row.parent_id = parent_id
        parent_changed = True

    for field in ('code_generator', 'remarks'):
        if field in body:
            value = body.get(field)
            setattr(row, field, None if value in (None, '') else str(value))

    if 'path' in body:
        value = body.get('path')
        row.path = None if value in (None, '') else str(value)
    elif creating or name_changed or parent_changed:
        row.path = _derive_category_path(row.name, row.parent_id)

    return None


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_category_list_api(request):
    if request.method == 'GET':
        parent_id = request.GET.get('parent_id')
        if parent_id in (None, ''):
            tree = _category_tree(root_parent_id=None)
        elif parent_id in ('0', 'null'):
            tree = _category_tree(root_parent_id=None)
        else:
            try:
                tree = _category_tree(root_parent_id=int(parent_id))
            except (TypeError, ValueError):
                return api_error('parent_id must be an integer.', status_code=400)
        return api_success('Product categories fetched successfully.', tree)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    missing = [f for f in ('id', 'name') if body.get(f) in (None, '')]
    if missing:
        return api_error(f'Missing required fields: {", ".join(missing)}', status_code=400)

    try:
        category_id = int(body.get('id'))
    except (TypeError, ValueError):
        return api_error('id must be an integer.', status_code=400)

    if Category.objects.filter(pk=category_id).exists():
        return api_error(f'Category id={category_id} already exists.', status_code=409)

    row = Category(id=category_id)
    err = _apply_category_fields(row, body, creating=True)
    if err is not None:
        return err

    try:
        row.save()
    except IntegrityError as exc:
        return api_error(f'Could not create category: {exc}', status_code=400)

    return api_success(
        'Product category created successfully.',
        _category_dict(row, include_children=False),
        status_code=201,
    )


def _category_in_use_by(category: Category) -> list:
    """Hard blockers: child categories and active products."""
    usages = []
    for child in category.children.all()[:50]:
        usages.append({
            'type': 'child_category',
            'field': 'parent_id',
            'id': child.id,
            'name': child.name,
            'path': child.path,
        })
    for p in category.products.filter(is_active=True)[:50]:
        usages.append({
            'type': 'product',
            'field': 'category_id',
            'id': p.id,
            'name': p.name,
            'is_active': True,
        })
    return usages


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def product_category_detail_api(request, pk: int):
    try:
        row = Category.objects.get(pk=pk)
    except Category.DoesNotExist:
        return api_error('Product category not found.', status_code=404)

    if request.method == 'GET':
        return api_success(
            'Product category fetched successfully.',
            _category_dict(row, include_children=False),
        )

    if request.method == 'DELETE':
        blockers = _category_in_use_by(row)
        if blockers:
            return api_error(
                'Category is in use (products or child categories) and cannot be deleted.',
                data={'in_use_by': blockers},
                status_code=409,
            )
        # Inactive products keep their row; clear FK so PROTECT allows delete.
        row.products.filter(is_active=False).update(category_id=None)
        row.delete()
        return api_success('Product category deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    if not body:
        return api_error('No fields to update.', status_code=400)

    err = _apply_category_fields(row, body, creating=False)
    if err is not None:
        return err

    try:
        row.save()
    except IntegrityError as exc:
        return api_error(f'Could not update category: {exc}', status_code=400)

    return api_success(
        'Product category updated successfully.',
        _category_dict(row, include_children=False),
    )


@require_POST
@csrf_exempt
def product_category_image_api(request, pk: int):
    try:
        row = Category.objects.get(pk=pk)
    except Category.DoesNotExist:
        return api_error('Product category not found.', status_code=404)

    uploaded = request.FILES.get('file') or request.FILES.get('image')
    if not uploaded:
        return api_error('Image file is required.', status_code=400)
    try:
        upload_category_image(row, uploaded)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    return api_success(
        'Product category image updated successfully.',
        _category_dict(row, include_children=False),
    )


@require_GET
def product_range_list_api(request):
    return api_success('Product ranges fetched successfully.', _rows(Range.objects.all()))


@require_GET
def product_sub_range_list_api(request):
    qs = SubRange.objects.all()
    range_id = request.GET.get('range_id')
    if range_id not in (None, ''):
        qs = qs.filter(range_id=range_id)
    rows = [
        {
            'id': row.id,
            'name': row.name,
            'range_id': row.range_id,
        }
        for row in qs.order_by('name')
    ]
    return api_success('Product sub-ranges fetched successfully.', rows)


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_unit_list_api(request):
    if request.method == 'GET':
        return api_success('Product units fetched successfully.', _rows(Unit.objects.all()))

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    name = (body.get('name') or '').strip()
    unit_id = body.get('id')
    if unit_id in (None, '') or not name:
        return api_error('Missing required fields: id, name', status_code=400)

    if Unit.objects.filter(pk=unit_id).exists():
        return api_error(f'Unit id={unit_id} already exists.', status_code=409)

    try:
        row = Unit.objects.create(id=unit_id, name=name)
    except IntegrityError as exc:
        return api_error(f'Could not create unit: {exc}', status_code=400)

    return api_success(
        'Product unit created successfully.',
        {'id': row.id, 'name': row.name},
        status_code=201,
    )


def _unit_in_use_by(unit: Unit) -> list:
    """Where this unit is still referenced (PROTECT blockers)."""
    usages = []
    for p in unit.products.all()[:50]:
        usages.append({
            'type': 'product',
            'field': 'unit_id',
            'id': p.id,
            'name': p.name,
            'is_active': p.is_active,
        })
    for p in unit.purchased_products.all()[:50]:
        usages.append({
            'type': 'product',
            'field': 'purchasing_unit_id',
            'id': p.id,
            'name': p.name,
            'is_active': p.is_active,
        })
    for c in unit.purchase_unit_categories.all()[:50]:
        usages.append({
            'type': 'category',
            'field': 'purchase_unit_id',
            'id': c.id,
            'name': c.name,
        })
    for s in unit.supplier_products_outer.select_related('product').all()[:50]:
        usages.append({
            'type': 'product_supplier',
            'field': 'outer_unit_id',
            'id': s.id,
            'product_id': s.product_id,
            'name': s.supplier_product_name,
            'is_active': s.product.is_active if s.product_id else None,
        })
    for s in unit.supplier_products_inner.select_related('product').all()[:50]:
        usages.append({
            'type': 'product_supplier',
            'field': 'inner_unit_id',
            'id': s.id,
            'product_id': s.product_id,
            'name': s.supplier_product_name,
            'is_active': s.product.is_active if s.product_id else None,
        })
    return usages


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def product_unit_detail_api(request, pk: int):
    try:
        row = Unit.objects.get(pk=pk)
    except Unit.DoesNotExist:
        return api_error('Product unit not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Product unit fetched successfully.', {'id': row.id, 'name': row.name})

    if request.method == 'DELETE':
        try:
            row.delete()
        except ProtectedError:
            return api_error(
                'Product unit is in use and cannot be deleted.',
                data={'in_use_by': _unit_in_use_by(row)},
                status_code=409,
            )
        return api_success('Product unit deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    if 'name' not in body:
        return api_error('Missing required fields: name', status_code=400)

    name = (body.get('name') or '').strip()
    if not name:
        return api_error('name cannot be empty.', status_code=400)

    row.name = name
    try:
        row.save(update_fields=['name'])
    except IntegrityError as exc:
        return api_error(f'Could not update unit: {exc}', status_code=400)

    return api_success('Product unit updated successfully.', {'id': row.id, 'name': row.name})


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def product_purchase_format_list_api(request):
    if request.method == 'GET':
        return api_success(
            'Purchase formats fetched successfully.',
            _rows(PurchaseShapeFormat.objects.all()),
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    name = (body.get('name') or '').strip()
    format_id = body.get('id')
    if format_id in (None, '') or not name:
        return api_error('Missing required fields: id, name', status_code=400)

    if PurchaseShapeFormat.objects.filter(pk=format_id).exists():
        return api_error(f'Purchase format id={format_id} already exists.', status_code=409)

    try:
        row = PurchaseShapeFormat.objects.create(id=format_id, name=name)
    except IntegrityError as exc:
        return api_error(f'Could not create purchase format: {exc}', status_code=400)

    return api_success('Purchase format created successfully.', _format_dict(row), status_code=201)


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def product_purchase_format_detail_api(request, pk: int):
    try:
        row = PurchaseShapeFormat.objects.get(pk=pk)
    except PurchaseShapeFormat.DoesNotExist:
        return api_error('Purchase format not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Purchase format fetched successfully.', _format_dict(row))

    if request.method == 'DELETE':
        try:
            row.delete()
        except ProtectedError:
            return api_error(
                'Purchase format is in use and cannot be deleted.',
                status_code=409,
            )
        return api_success('Purchase format deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    if 'name' not in body:
        return api_error('Missing required fields: name', status_code=400)

    name = (body.get('name') or '').strip()
    if not name:
        return api_error('name cannot be empty.', status_code=400)

    row.name = name
    try:
        row.save(update_fields=['name'])
    except IntegrityError as exc:
        return api_error(f'Could not update purchase format: {exc}', status_code=400)

    return api_success('Purchase format updated successfully.', _format_dict(row))


@require_GET
def product_packaging_type_list_api(request):
    return api_success(
        'Packaging types fetched successfully.',
        _rows(PackagingType.objects.all()),
    )


@require_GET
def product_physical_state_list_api(request):
    return api_success(
        'Physical states fetched successfully.',
        _rows(PhysicalState.objects.all()),
    )


@require_GET
def product_delivery_state_list_api(request):
    return api_success(
        'Delivery states fetched successfully.',
        _rows(DeliveryState.objects.all()),
    )


@require_GET
def product_allergen_code_list_api(request):
    rows = [
        {'code': code.value, 'name': code.label}
        for code in AllergenCode
    ]
    return api_success('Allergen codes fetched successfully.', rows)


@require_GET
def product_list_fromcontainer_api(request, container_id: int):
    """Products whose source container (from) is this location/department."""
    rows = [
        product_list_dict(p)
        for p in active_products(source_container_id=container_id).order_by('name')
    ]
    return api_success('Products fetched successfully.', rows)