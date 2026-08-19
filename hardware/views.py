import json

from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from core.api_response import error_response, success_response
from hardware.media import delete_post, media_url, post_dict, upload_post
from hardware.models import (
    HardwareDevice,
    HardwareDeviceAction,
    HardwareDeviceEvent,
    HardwareDeviceMessage,
    HardwareDevicePost,
    HardwareDeviceStatus,
)
from hardware.services import (
    device_dict,
    event_dict,
    get_device,
    message_dict,
    next_code,
    normalise_code,
    pull_pending_messages,
    serial_from_request,
    touch_from_request,
)
from users_rbac.auth import attach_user, require_admin
from users_rbac.grants import is_admin_user
from users_rbac.models import RbacUser
from users_rbac.permissions import require_any_admin


def _parse_body(request):
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _device_qs():
    return HardwareDevice.objects.select_related(
        'home_location', 'assigned_user', 'last_user', 'last_location',
    )


def _with_covers(rows: list) -> list:
    if not rows:
        return rows
    ids = [row.id for row in rows]
    counts = dict(
        HardwareDevicePost.objects.filter(device_id__in=ids)
        .values('device_id')
        .annotate(n=Count('id'))
        .values_list('device_id', 'n')
    )
    latest = {}
    for post in HardwareDevicePost.objects.filter(device_id__in=ids).order_by('-created_at'):
        if post.device_id not in latest:
            latest[post.device_id] = post
    for row in rows:
        row._post_count = counts.get(row.id, 0)
        row._cover_url = media_url(latest.get(row.id))
    return rows


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def devices_collection(request):
    denied = attach_user(request)
    if denied:
        return denied
    if request.method == 'GET':
        qs = _device_qs()
        location_id = (request.GET.get('location_id') or '').strip()
        if location_id:
            qs = qs.filter(home_location_id=location_id)
        assigned = (request.GET.get('assigned_user_id') or '').strip()
        if assigned:
            qs = qs.filter(assigned_user_id=assigned)
        return success_response(
            'Devices fetched successfully.',
            data=[device_dict(row) for row in _with_covers(list(qs))],
        )

    denied = require_any_admin(request)
    if denied:
        return denied
    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.', status_code=400)
    try:
        code = normalise_code(body['code']) if body.get('code') else next_code()
    except (KeyError, ValueError) as exc:
        return error_response(str(exc) if str(exc) else 'code is required.', status_code=400)
    serial = (body.get('serial') or '').strip() or None
    if HardwareDevice.objects.filter(code=code).exists():
        return error_response(f'{code} is already registered.', status_code=409)
    if serial and HardwareDevice.objects.filter(serial=serial).exists():
        return error_response('That serial is already registered.', status_code=409)
    assigned_id = body.get('assigned_user_id')
    if assigned_id not in (None, ''):
        if not RbacUser.objects.filter(pk=assigned_id).exists():
            return error_response("We couldn't find that user.", status_code=404)
    row = HardwareDevice.objects.create(
        code=code,
        serial=serial,
        model=(body.get('model') or 'TC22F')[:32],
        nickname=(body.get('nickname') or '')[:64],
        home_location_id=body.get('home_location_id') or None,
        assigned_user_id=assigned_id or None,
        status=body.get('status') or HardwareDeviceStatus.ACTIVE,
        identity_json=body.get('identity_json'),
    )
    if assigned_id:
        HardwareDeviceEvent.objects.create(
            device=row,
            user_id=assigned_id,
            action=HardwareDeviceAction.ALLOCATE,
            request_path=request.path,
            detail_json={'assigned_user_id': assigned_id},
        )
    return success_response(
        'Device registered.',
        data=device_dict(_with_covers([_device_qs().get(pk=row.pk)])[0]),
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['GET', 'PATCH'])
def device_detail(request, ident: str):
    row = get_device(ident)
    if row is None:
        return error_response("We couldn't find that device.", status_code=404)

    if request.method == 'GET':
        denied = attach_user(request)
        if denied:
            return denied
        return success_response(
            'Device fetched successfully.',
            data=device_dict(_with_covers([_device_qs().get(pk=row.pk)])[0]),
        )

    denied = attach_user(request)
    if denied:
        return denied
    denied = require_any_admin(request)
    if denied:
        return denied
    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.', status_code=400)

    updates = []
    if 'code' in body:
        try:
            code = normalise_code(body.get('code') or '')
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        if HardwareDevice.objects.filter(code=code).exclude(pk=row.pk).exists():
            return error_response(f'{code} is already registered.', status_code=409)
        row.code = code
        updates.append('code')
    if 'nickname' in body:
        row.nickname = str(body.get('nickname') or '')[:64]
        updates.append('nickname')
    if 'home_location_id' in body:
        row.home_location_id = body.get('home_location_id') or None
        updates.append('home_location')
    if 'status' in body:
        status = body.get('status')
        if status not in HardwareDeviceStatus.values:
            return error_response('Unknown status.', status_code=400)
        row.status = status
        updates.append('status')
    if 'assigned_user_id' in body:
        assigned_id = body.get('assigned_user_id')
        if assigned_id in (None, ''):
            row.assigned_user_id = None
        elif not RbacUser.objects.filter(pk=assigned_id).exists():
            return error_response("We couldn't find that user.", status_code=404)
        else:
            row.assigned_user_id = assigned_id
        updates.append('assigned_user')
        HardwareDeviceEvent.objects.create(
            device=row,
            user_id=row.assigned_user_id,
            action=HardwareDeviceAction.ALLOCATE,
            request_path=request.path,
            detail_json={'assigned_user_id': row.assigned_user_id},
        )
    if 'serial' in body:
        serial = (body.get('serial') or '').strip() or None
        if serial and HardwareDevice.objects.filter(serial=serial).exclude(pk=row.pk).exists():
            return error_response('That serial is already registered.', status_code=409)
        row.serial = serial
        updates.append('serial')
    if not updates:
        return error_response('No fields to update.', status_code=400)
    row.save(update_fields=updates + ['updated_at'])
    return success_response(
        'Device updated.',
        data=device_dict(_with_covers([_device_qs().get(pk=row.pk)])[0]),
    )


@csrf_exempt
@require_GET
@require_admin
def usage_list(request):
    qs = HardwareDeviceEvent.objects.select_related('device', 'user').all()
    code = (request.GET.get('code') or '').strip()
    serial = (request.GET.get('serial') or '').strip()
    user_id = (request.GET.get('user_id') or '').strip()
    location_id = (request.GET.get('location_id') or '').strip()
    ident = (request.GET.get('device') or '').strip()
    if code:
        qs = qs.filter(device__code__iexact=code)
    if serial:
        qs = qs.filter(device__serial=serial)
    if ident:
        qs = qs.filter(Q(device__code__iexact=ident) | Q(device__serial=ident))
    if user_id:
        qs = qs.filter(user_id=user_id)
    if location_id:
        qs = qs.filter(location_id=location_id)
    dt_from = (request.GET.get('from') or '').strip()
    dt_to = (request.GET.get('to') or '').strip()
    if dt_from:
        parsed = parse_datetime(dt_from.replace('Z', '+00:00'))
        if parsed is None:
            return error_response('Invalid from datetime.', status_code=400)
        qs = qs.filter(at__gte=parsed)
    if dt_to:
        parsed = parse_datetime(dt_to.replace('Z', '+00:00'))
        if parsed is None:
            return error_response('Invalid to datetime.', status_code=400)
        qs = qs.filter(at__lte=parsed)
    try:
        limit = min(max(int(request.GET.get('limit') or 50), 1), 200)
        offset = max(int(request.GET.get('offset') or 0), 0)
    except ValueError:
        return error_response('limit and offset must be integers.', status_code=400)
    count = qs.count()
    items = [event_dict(row) for row in qs[offset : offset + limit]]
    return success_response(
        'Device usage fetched.',
        data={'items': items, 'count': count, 'limit': limit, 'offset': offset},
    )


def _page(request):
    try:
        limit = min(max(int(request.GET.get('limit') or 50), 1), 200)
        offset = max(int(request.GET.get('offset') or 0), 0)
    except ValueError:
        raise ValueError('limit and offset must be integers.')
    return limit, offset


@csrf_exempt
@require_GET
def feed_list(request):
    denied = attach_user(request)
    if denied:
        return denied
    qs = HardwareDevicePost.objects.select_related('device', 'user')
    code = (request.GET.get('code') or request.GET.get('device') or '').strip()
    user_id = (request.GET.get('user_id') or '').strip()
    if code:
        device = get_device(code)
        if device is None:
            return error_response("We couldn't find that device.", status_code=404)
        qs = qs.filter(device=device)
    if user_id:
        qs = qs.filter(user_id=user_id)
    dt_from = (request.GET.get('from') or '').strip()
    dt_to = (request.GET.get('to') or '').strip()
    if dt_from:
        parsed = parse_datetime(dt_from.replace('Z', '+00:00'))
        if parsed is None:
            return error_response('Invalid from datetime.', status_code=400)
        qs = qs.filter(created_at__gte=parsed)
    if dt_to:
        parsed = parse_datetime(dt_to.replace('Z', '+00:00'))
        if parsed is None:
            return error_response('Invalid to datetime.', status_code=400)
        qs = qs.filter(created_at__lte=parsed)
    try:
        limit, offset = _page(request)
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    count = qs.count()
    items = [post_dict(row) for row in qs[offset : offset + limit]]
    return success_response(
        'Device feed fetched.',
        data={'items': items, 'count': count, 'limit': limit, 'offset': offset},
    )


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def device_posts(request, ident: str):
    device = get_device(ident)
    if device is None:
        return error_response("We couldn't find that device.", status_code=404)
    denied = attach_user(request)
    if denied:
        return denied
    if request.method == 'GET':
        try:
            limit, offset = _page(request)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        qs = HardwareDevicePost.objects.filter(device=device).select_related(
            'device', 'user',
        )
        count = qs.count()
        items = [post_dict(row) for row in qs[offset : offset + limit]]
        return success_response(
            'Device posts fetched.',
            data={'items': items, 'count': count, 'limit': limit, 'offset': offset},
        )
    uploaded = request.FILES.get('file') or request.FILES.get('image')
    if not uploaded:
        return error_response('File is required (multipart field: file).', status_code=400)
    try:
        row = upload_post(
            device,
            uploaded,
            user=request.rbac_user,
            caption=request.POST.get('caption') or '',
        )
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    row = HardwareDevicePost.objects.select_related('device', 'user').get(pk=row.pk)
    return success_response('Device post created.', data=post_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['DELETE'])
def device_post_detail(request, ident: str, post_id: int):
    denied = attach_user(request)
    if denied:
        return denied
    device = get_device(ident)
    if device is None:
        return error_response("We couldn't find that device.", status_code=404)
    row = HardwareDevicePost.objects.filter(pk=post_id, device=device).first()
    if row is None:
        return error_response("We couldn't find that post.", status_code=404)
    if row.user_id != request.rbac_user.id and not is_admin_user(request.rbac_user):
        return error_response('You do not have permission to do that.', status_code=403)
    delete_post(row)
    return success_response('Device post deleted.', data={'id': post_id})


@csrf_exempt
@require_http_methods(['POST'])
def heartbeat(request):
    denied = attach_user(request)
    if denied:
        return denied
    body = _parse_body(request)
    if body is None:
        if request.body:
            return error_response('Invalid request body.', status_code=400)
        body = {}
    if not serial_from_request(request, body):
        return error_response('X-Device-Serial is required.', status_code=400)
    device = touch_from_request(
        request,
        action=HardwareDeviceAction.HEARTBEAT,
        body=body,
        record_event=False,
    )
    device = _device_qs().get(pk=device.pk)
    data = device_dict(device)
    data['messages'] = pull_pending_messages(device)
    return success_response('Heartbeat recorded.', data=data)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def device_messages(request, ident: str):
    device = get_device(ident)
    if device is None:
        return error_response("We couldn't find that device.", status_code=404)
    denied = attach_user(request)
    if denied:
        return denied
    denied = require_any_admin(request)
    if denied:
        return denied
    if request.method == 'GET':
        qs = HardwareDeviceMessage.objects.filter(device=device).select_related(
            'device', 'created_by',
        )
        return success_response(
            'Device messages fetched.',
            data=[message_dict(row) for row in qs],
        )
    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.', status_code=400)
    title = str(body.get('title') or '').strip()
    if not title:
        return error_response('title is required.', status_code=400)
    row = HardwareDeviceMessage.objects.create(
        device=device,
        created_by=request.rbac_user,
        title=title[:128],
        body=str(body.get('body') or '')[:512],
    )
    row = HardwareDeviceMessage.objects.select_related('device', 'created_by').get(pk=row.pk)
    return success_response('Message sent.', data=message_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def message_ack(request, message_id: int):
    denied = attach_user(request)
    if denied:
        return denied
    serial = serial_from_request(request)
    if not serial:
        return error_response('X-Device-Serial is required.', status_code=400)
    row = (
        HardwareDeviceMessage.objects.select_related('device', 'created_by')
        .filter(pk=message_id, device__serial=serial)
        .first()
    )
    if row is None:
        return error_response("We couldn't find that message.", status_code=404)
    if row.acked_at is None:
        row.acked_at = timezone.now()
        row.save(update_fields=['acked_at'])
    return success_response('Message acknowledged.', data=message_dict(row))

