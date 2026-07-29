import json

from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.presentation import location_detail_dict, location_list_dict
from locations.services.location_service import (
    create_location,
    delete_location,
    update_location,
)
from locations.utils.api_response import api_error, api_success
from locations.utils.location_api import (
    json_location_detail,
    json_location_list,
    location_queryset,
)


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, 'messages'):
        return '; '.join(str(m) for m in exc.messages)
    return str(exc)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def location_list_api(request):
    if request.method == 'GET':
        return json_location_list(
            request,
            location_queryset(),
            location_list_dict,
            message='Location list fetched successfully.',
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    location_id = body.get('id')
    name = body.get('name')
    if location_id in (None, '') or name in (None, ''):
        return api_error('Missing required fields: id, name', status_code=400)

    try:
        location = create_location(
            location_id=int(location_id),
            name=name,
            external_code=body.get('external_code'),
            visible=bool(body.get('visible', True)),
            static=bool(body.get('static', False)),
            locked=bool(body.get('locked', False)),
            remarks=body.get('remarks'),
            po_remarks=body.get('po_remarks'),
            roles=body.get('roles'),
            features=body.get('features'),
            stock_profile=body.get('stock_profile'),
            zone_parent_id=body.get('zone_parent_id'),
            subordinate_parent_id=body.get('subordinate_parent_id'),
        )
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)
    except ValidationError as exc:
        return api_error(_validation_message(exc), status_code=400)

    location = location_queryset().get(pk=location.id)
    return api_success(
        'Location created successfully.',
        location_detail_dict(location),
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def location_detail_api(request, location_id: int):
    if request.method == 'GET':
        return json_location_detail(
            request,
            location_id,
            location_queryset(),
            location_detail_dict,
            message='Location fetched successfully.',
            not_found_message='Location not found.',
        )

    if request.method == 'DELETE':
        try:
            delete_location(location_id)
        except ValidationError as exc:
            return api_error(_validation_message(exc), status_code=404)
        return api_success('Location deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    hierarchy = None
    if 'zone_parent_id' in body or 'subordinate_parent_id' in body:
        hierarchy = {}
        if 'zone_parent_id' in body:
            hierarchy['zone_parent_id'] = body['zone_parent_id']
        if 'subordinate_parent_id' in body:
            hierarchy['subordinate_parent_id'] = body['subordinate_parent_id']

    try:
        update_location(
            location_id,
            name=body.get('name') if 'name' in body else None,
            external_code=body.get('external_code') if 'external_code' in body else None,
            visible=body.get('visible') if 'visible' in body else None,
            static=body.get('static') if 'static' in body else None,
            locked=body.get('locked') if 'locked' in body else None,
            remarks=body.get('remarks') if 'remarks' in body else None,
            po_remarks=body.get('po_remarks') if 'po_remarks' in body else None,
            roles=body.get('roles') if 'roles' in body else None,
            features=body.get('features') if 'features' in body else None,
            stock_profile=body.get('stock_profile') if 'stock_profile' in body else None,
            hierarchy=hierarchy,
        )
    except ValidationError as exc:
        msg = _validation_message(exc)
        status = 404 if 'not found' in msg.lower() else 400
        return api_error(msg, status_code=status)

    location = location_queryset().get(pk=location_id)
    return api_success('Location updated successfully.', location_detail_dict(location))
