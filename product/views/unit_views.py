import json
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from product.models import Unit, UnitGroup


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
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Invalid decimal for {field_name}.')


def unit_dict(unit: Unit) -> dict:
    return {
        'id': unit.id,
        'name': unit.name,
        'unit_group': unit.unit_group,
        'to_base_factor': (
            str(unit.to_base_factor) if unit.to_base_factor is not None else None
        ),
    }


@require_http_methods(['GET', 'POST'])
@csrf_exempt
def unit_collection_api(request):
    if request.method == 'GET':
        units = Unit.objects.all().order_by('name')
        return api_success(
            'Unit list fetched successfully.',
            {'count': units.count(), 'results': [unit_dict(row) for row in units]},
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    if body.get('id') in (None, '') or body.get('name') in (None, ''):
        return api_error('Missing required fields: id, name', status_code=400)

    unit_group = body.get('unit_group')
    if unit_group not in (None, '') and unit_group not in UnitGroup.values:
        return api_error('Invalid unit_group.', status_code=400)

    try:
        to_base_factor = _parse_decimal(body.get('to_base_factor'), 'to_base_factor')
        if to_base_factor is not None and to_base_factor <= 0:
            return api_error('to_base_factor must be greater than 0.', status_code=400)
        unit = Unit.objects.create(
            id=body['id'],
            name=body['name'],
            unit_group=unit_group or None,
            to_base_factor=to_base_factor,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not create unit: {exc}', status_code=400)

    return api_success('Unit created successfully.', unit_dict(unit), status_code=201)


@require_http_methods(['GET', 'PATCH', 'DELETE'])
@csrf_exempt
def unit_detail_api(request, pk: int):
    try:
        unit = Unit.objects.get(pk=pk)
    except Unit.DoesNotExist:
        return api_error('Unit not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Unit fetched successfully.', unit_dict(unit))

    if request.method == 'DELETE':
        try:
            unit.delete()
        except IntegrityError:
            return api_error(
                'Unit is in use and cannot be deleted.',
                status_code=409,
            )
        return api_success('Unit deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        if 'name' in body:
            if body['name'] in (None, ''):
                return api_error('name cannot be empty.', status_code=400)
            unit.name = body['name']

        if 'unit_group' in body:
            unit_group = body['unit_group']
            if unit_group in (None, ''):
                unit.unit_group = None
            elif unit_group in UnitGroup.values:
                unit.unit_group = unit_group
            else:
                return api_error('Invalid unit_group.', status_code=400)

        if 'to_base_factor' in body:
            to_base_factor = _parse_decimal(body['to_base_factor'], 'to_base_factor')
            if to_base_factor is not None and to_base_factor <= 0:
                return api_error('to_base_factor must be greater than 0.', status_code=400)
            unit.to_base_factor = to_base_factor

        unit.save()
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except IntegrityError as exc:
        return api_error(f'Could not update unit: {exc}', status_code=400)

    return api_success('Unit updated successfully.', unit_dict(unit))
