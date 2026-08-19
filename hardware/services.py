import re

from django.utils import timezone

from hardware.models import HardwareDevice, HardwareDeviceAction, HardwareDeviceEvent
from users_rbac.auth import attach_user

CODE_RE = re.compile(r'^GUN-\d{1,4}$', re.IGNORECASE)
SERIAL_MAX = 32


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
        'last_user_id': row.last_user_id,
        'last_username': last_user.username if last_user else None,
        'last_location_id': row.last_location_id,
        'status': row.status,
        'cover_url': getattr(row, '_cover_url', None),
        'post_count': getattr(row, '_post_count', 0),
        'identity_json': row.identity_json,
    }


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
    now = timezone.now()

    device = HardwareDevice.objects.filter(serial=serial).first()
    created = False
    if device is None:
        device = HardwareDevice.objects.create(
            code=next_code(),
            serial=serial,
            nickname=nickname or '',
            model='TC22F',
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
        device.save(update_fields=['last_seen_at', 'last_user', 'last_location'])
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
