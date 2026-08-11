import json

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from locations.audit_log import (
    audit_snapshot,
    capture_location_audit,
    event_dict,
)
from locations.location_images import upload_location_image
from locations.models import Location, LocationAuditAction, LocationAuditEvent
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
from users_rbac.auth import attach_user
from users_rbac.permissions import require_any_admin

MAX_AUDIT_LIMIT = 200
DEFAULT_AUDIT_LIMIT = 50


def _require_admin(request):
    """Mutations need Cognito admin (send X-API-Token + Authorization: Bearer <jwt>)."""
    denied = attach_user(request)
    if denied:
        return denied
    return require_any_admin(request)


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

    denied = _require_admin(request)
    if denied:
        return denied

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    name = body.get('name')
    if name in (None, ''):
        return api_error('Missing required field: name', status_code=400)

    location_id = body.get('id')
    if location_id in (None, ''):
        location_id = None
    else:
        try:
            location_id = int(location_id)
        except (TypeError, ValueError):
            return api_error('id must be a number when provided.', status_code=400)

    try:
        location = create_location(
            location_id=location_id,
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
            supplier_profile=body.get('supplier_profile'),
            zone_parent_id=body.get('zone_parent_id'),
            subordinate_parent_id=body.get('subordinate_parent_id'),
        )
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)
    except ValidationError as exc:
        return api_error(_validation_message(exc), status_code=400)

    location = location_queryset().get(pk=location.id)
    after = audit_snapshot(location)
    capture_location_audit(
        request,
        location_id=location.id,
        action=LocationAuditAction.CREATE,
        after_data=after,
    )
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

    denied = _require_admin(request)
    if denied:
        return denied

    if request.method == 'DELETE':
        try:
            before_loc = location_queryset().get(pk=location_id)
        except Location.DoesNotExist:
            return api_error('Location not found.', status_code=404)
        before = audit_snapshot(before_loc)
        try:
            delete_location(location_id)
        except ValidationError as exc:
            return api_error(_validation_message(exc), status_code=404)
        capture_location_audit(
            request,
            location_id=location_id,
            action=LocationAuditAction.DELETE,
            before_data=before,
            location_name=before.get('name'),
        )
        return api_success('Location deleted successfully.', data=None)

    try:
        before = audit_snapshot(location_queryset().get(pk=location_id))
    except Location.DoesNotExist:
        return api_error('Location not found.', status_code=404)

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
            supplier_profile=(
                body.get('supplier_profile') if 'supplier_profile' in body else None
            ),
            hierarchy=hierarchy,
        )
    except ValidationError as exc:
        msg = _validation_message(exc)
        status = 404 if 'not found' in msg.lower() else 400
        return api_error(msg, status_code=status)

    location = location_queryset().get(pk=location_id)
    after = audit_snapshot(location)
    capture_location_audit(
        request,
        location_id=location_id,
        action=LocationAuditAction.UPDATE,
        before_data=before,
        after_data=after,
    )
    return api_success('Location updated successfully.', location_detail_dict(location))


@csrf_exempt
@require_POST
def location_image_api(request, location_id: int):
    denied = _require_admin(request)
    if denied:
        return denied

    try:
        location = Location.objects.get(pk=location_id)
    except Location.DoesNotExist:
        return api_error('Location not found.', status_code=404)

    before = {'image_key': location.image_key}
    uploaded = request.FILES.get('file') or request.FILES.get('image')
    if not uploaded:
        return api_error('Image file is required (multipart field: file).', status_code=400)
    try:
        upload_location_image(location, uploaded)
    except ValueError as exc:
        return api_error(str(exc), status_code=400)

    location = location_queryset().get(pk=location.id)
    capture_location_audit(
        request,
        location_id=location.id,
        action=LocationAuditAction.IMAGE_UPDATE,
        before_data=before,
        after_data={'image_key': location.image_key},
        location_name=location.name,
    )
    return api_success(
        'Location image updated successfully.',
        location_detail_dict(location),
    )


@require_GET
def location_audit_api(request, location_id: int):
    denied = _require_admin(request)
    if denied:
        return denied

    try:
        limit = int(request.GET.get('limit') or DEFAULT_AUDIT_LIMIT)
        offset = int(request.GET.get('offset') or 0)
    except ValueError:
        return api_error('limit and offset must be integers.', status_code=400)
    if limit < 1 or offset < 0:
        return api_error('limit must be >= 1 and offset >= 0.', status_code=400)
    limit = min(limit, MAX_AUDIT_LIMIT)

    qs = LocationAuditEvent.objects.filter(location_id=location_id)
    total = qs.count()
    rows = qs[offset : offset + limit]
    return api_success(
        'Location audit fetched successfully.',
        {
            'count': total,
            'limit': limit,
            'offset': offset,
            'results': [event_dict(row) for row in rows],
        },
    )


@require_GET
def location_audit_list_api(request):
    denied = _require_admin(request)
    if denied:
        return denied

    try:
        limit = int(request.GET.get('limit') or DEFAULT_AUDIT_LIMIT)
        offset = int(request.GET.get('offset') or 0)
    except ValueError:
        return api_error('limit and offset must be integers.', status_code=400)
    if limit < 1 or offset < 0:
        return api_error('limit must be >= 1 and offset >= 0.', status_code=400)
    limit = min(limit, MAX_AUDIT_LIMIT)

    qs = LocationAuditEvent.objects.all()
    location_id = request.GET.get('location_id')
    if location_id not in (None, ''):
        try:
            qs = qs.filter(location_id=int(location_id))
        except (TypeError, ValueError):
            return api_error('location_id must be an integer.', status_code=400)
    actor = request.GET.get('actor') or request.GET.get('actor_sub')
    if actor:
        qs = qs.filter(Q(actor_sub=actor) | Q(actor_username=actor))
    action = request.GET.get('action')
    if action:
        qs = qs.filter(action=action)

    total = qs.count()
    rows = qs[offset : offset + limit]
    return api_success(
        'Location audit list fetched successfully.',
        {
            'count': total,
            'limit': limit,
            'offset': offset,
            'results': [event_dict(row) for row in rows],
        },
    )
