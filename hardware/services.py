import re
from datetime import timedelta

from django.utils import timezone

from hardware.models import (
    HardwareDevice,
    HardwareDeviceAction,
    HardwareDeviceEvent,
    HardwareDeviceMessage,
)
from users_rbac.auth import attach_user, client_ip

CODE_RE = re.compile(r'^GUN-\d{1,4}$', re.IGNORECASE)
SERIAL_MAX = 32
ONLINE_AFTER = timedelta(seconds=30)
IDLE_AFTER = timedelta(minutes=5)
PENDING_CAP = 20


def serial_from_request(request, body: dict | None = None) -> str | None:
    raw = (
        request.headers.get('X-Device-Serial')
        or request.META.get('HTTP_X_DEVICE_SERIAL')
        or (body or {}).get('device_serial')
        or ''
    )
    serial = str(raw).strip()
    if not serial or len(serial) > SERIAL_MAX:
        return None
    return serial


def nickname_from_request(request, body: dict | None = None) -> str | None:
    raw = (
        request.headers.get('X-Device-Nickname')
        or request.META.get('HTTP_X_DEVICE_NICKNAME')
        or (body or {}).get('device_nickname')
        or ''
    )
    nickname = str(raw).strip()[:64]
    return nickname or None


def normalise_code(raw: str) -> str:
    code = (raw or '').strip().upper()
    if not CODE_RE.match(code):
        raise ValueError('code must look like GUN-03.')
    prefix, num = code.split('-', 1)
    return f'{prefix}-{int(num):02d}' if len(num) <= 2 else f'{prefix}-{int(num)}'


def next_code() -> str:
    codes = HardwareDevice.objects.filter(code__startswith='GUN-').values_list(
        'code', flat=True,
    )
    nums = []
    for code in codes:
        try:
            nums.append(int(code.split('-', 1)[1]))
        except (IndexError, ValueError):
            continue
    return f'GUN-{max(nums, default=0) + 1:02d}'


def get_device(ident: str) -> HardwareDevice | None:
    ident = (ident or '').strip()
    if not ident:
        return None
    return (
        HardwareDevice.objects.filter(code__iexact=ident).first()
        or HardwareDevice.objects.filter(serial=ident).first()
    )


def presence_for(row: HardwareDevice) -> str:
    if not row.last_seen_at:
        return 'offline'
    age = timezone.now() - row.last_seen_at
    if age < ONLINE_AFTER:
        return 'online'
    if age < IDLE_AFTER:
        return 'idle'
    return 'offline'


def device_dict(row: HardwareDevice) -> dict:
    assigned = row.assigned_user
    last_user = row.last_user
    return {
        'id': row.id,
        'code': row.code,
        'serial': row.serial,
        'model': row.model,
        'nickname': row.nickname,
        'zebra_uuid': row.zebra_uuid,
        'bt_mac': row.bt_mac,
        'home_location_id': row.home_location_id,
        'home_location_name': row.home_location.name if row.home_location_id else None,
        'assigned_user_id': row.assigned_user_id,
        'assigned_username': assigned.username if assigned else None,
        'assigned_display_name': assigned.display_name if assigned else None,
        'last_seen_at': row.last_seen_at.isoformat() if row.last_seen_at else None,
        'last_ip': row.last_ip,
        'last_screen': row.last_screen or '',
        'presence': presence_for(row),
        'last_user_id': row.last_user_id,
        'last_username': last_user.username if last_user else None,
        'last_location_id': row.last_location_id,
        'status': row.status,
        'cover_url': getattr(row, '_cover_url', None),
        'post_count': getattr(row, '_post_count', 0),
        'identity_json': row.identity_json,
    }


def message_dict(row: HardwareDeviceMessage) -> dict:
    author = row.created_by
    device = row.device
    return {
        'id': row.id,
        'device_id': row.device_id,
        'device_code': device.code if device else None,
        'title': row.title,
        'body': row.body,
        'created_by_id': row.created_by_id,
        'created_by_username': author.username if author else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'delivered_at': row.delivered_at.isoformat() if row.delivered_at else None,
        'acked_at': row.acked_at.isoformat() if row.acked_at else None,
    }


def pull_pending_messages(device: HardwareDevice) -> list[dict]:
    rows = list(
        HardwareDeviceMessage.objects.filter(device=device, acked_at__isnull=True)
        .select_related('device', 'created_by')
        .order_by('created_at')[:PENDING_CAP]
    )
    now = timezone.now()
    unmarked = [row.id for row in rows if row.delivered_at is None]
    if unmarked:
        HardwareDeviceMessage.objects.filter(pk__in=unmarked).update(delivered_at=now)
        for row in rows:
            if row.delivered_at is None:
                row.delivered_at = now
    return [message_dict(row) for row in rows]


def event_dict(row: HardwareDeviceEvent) -> dict:
    user = row.user
    device = row.device
    return {
        'id': row.id,
        'at': row.at.isoformat() if row.at else None,
        'action': row.action,
        'device_id': row.device_id,
        'device_code': device.code if device else None,
        'device_serial': device.serial if device else None,
        'user_id': row.user_id,
        'username': user.username if user else None,
        'display_name': user.display_name if user else None,
        'location_id': row.location_id,
        'request_path': row.request_path,
        'detail_json': row.detail_json,
    }


def codes_for_serials(serials) -> dict[str, str]:
    wanted = [s for s in serials if s]
    if not wanted:
        return {}
    return dict(
        HardwareDevice.objects.filter(serial__in=wanted).values_list('serial', 'code')
    )


def ip_from_request(request, body: dict | None = None) -> str | None:
    raw = (
        request.headers.get('X-Device-Ip')
        or request.META.get('HTTP_X_DEVICE_IP')
        or (body or {}).get('device_ip')
        or client_ip(request)
        or ''
    )
    ip = str(raw).strip()[:45]
    return ip or None


def _location_id(request, body: dict | None, location_id) -> int | None:
    if location_id not in (None, ''):
        try:
            return int(location_id)
        except (TypeError, ValueError):
            return None
    raw = (body or {}).get('location_id') or request.GET.get('location_id')
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def touch_from_request(
    request,
    *,
    action: str,
    body: dict | None = None,
    location_id=None,
    user=None,
    record_event: bool = True,
) -> HardwareDevice | None:
    """No-op when the client is a computer (no serial header)."""
    serial = serial_from_request(request, body)
    if not serial:
        return None
    attach_user(request, missing='ok', invalid='ok')
    user = user or getattr(request, 'rbac_user', None)
    nickname = nickname_from_request(request, body)
    loc_id = _location_id(request, body, location_id)
    ip = ip_from_request(request, body)
    screen = str((body or {}).get('screen') or '').strip()[:32] or None
    now = timezone.now()

    device = HardwareDevice.objects.filter(serial=serial).first()
    created = False
    if device is None:
        device = HardwareDevice.objects.create(
            code=next_code(),
            serial=serial,
            nickname=nickname or '',
            model='TC22F',
            last_ip=ip,
            last_screen=screen or '',
        )
        created = True
    else:
        updates = ['last_seen_at', 'updated_at']
        if nickname and device.nickname != nickname:
            device.nickname = nickname
            updates.append('nickname')
        if user and device.last_user_id != user.id:
            device.last_user = user
            updates.append('last_user')
        if loc_id is not None and device.last_location_id != loc_id:
            device.last_location_id = loc_id
            updates.append('last_location')
        if ip and device.last_ip != ip:
            device.last_ip = ip
            updates.append('last_ip')
        if screen is not None and device.last_screen != screen:
            device.last_screen = screen
            updates.append('last_screen')
        device.last_seen_at = now
        if user and not device.last_user_id:
            device.last_user = user
            if 'last_user' not in updates:
                updates.append('last_user')
        device.save(update_fields=updates)

    if created:
        device.last_seen_at = now
        device.last_user = user
        device.last_location_id = loc_id
        device.last_ip = ip
        if screen:
            device.last_screen = screen
        device.save(
            update_fields=['last_seen_at', 'last_user', 'last_location', 'last_ip', 'last_screen'],
        )
        HardwareDeviceEvent.objects.create(
            device=device,
            user=user,
            location_id=loc_id,
            action=HardwareDeviceAction.ENROLL,
            request_path=request.path,
            detail_json={'serial': serial},
        )

    if record_event:
        HardwareDeviceEvent.objects.create(
            device=device,
            user=user,
            location_id=loc_id,
            action=action,
            request_path=request.path,
            detail_json={'serial': serial},
        )
    return device
