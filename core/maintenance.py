import json
import logging
import urllib.error
import urllib.request
from datetime import datetime

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.api_response import error_response, success_response
from core.models import MaintenanceNotice
from users_rbac.auth import attach_user
from users_rbac.permissions import require_any_admin

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 3


def _row() -> MaintenanceNotice:
    row, _created = MaintenanceNotice.objects.get_or_create(pk=1)
    return row


def maintenance_dict(row: MaintenanceNotice | None = None) -> dict:
    row = row or MaintenanceNotice.objects.filter(pk=1).first()
    if row is None:
        return {
            'is_active': False,
            'message': None,
            'resume_at': None,
            'updated_by_username': None,
            'updated_at': None,
        }
    return {
        'is_active': row.is_active,
        'message': row.message or None,
        'resume_at': row.resume_at.isoformat() if row.resume_at else None,
        'updated_by_username': row.updated_by_username or None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
    }


def _parse_resume_at(value):
    if value in (None, ''):
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid resume_at datetime.') from exc


def _fire_webhook(row: MaintenanceNotice):
    url = (getattr(settings, 'MAINTENANCE_WEBHOOK_URL', None) or '').strip()
    if not url:
        return
    payload = {
        'text': row.message,
        'message': row.message,
        'resume_at': row.resume_at.isoformat() if row.resume_at else None,
    }
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT)
    except (urllib.error.URLError, TimeoutError, OSError):
        logger.exception('Maintenance webhook failed.')


@csrf_exempt
@require_http_methods(['GET', 'PUT'])
def maintenance_view(request):
    denied = attach_user(request)
    if denied:
        return denied
    if request.method == 'GET':
        return success_response('Maintenance status fetched.', data=maintenance_dict())

    denied = require_any_admin(request)
    if denied:
        return denied
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return error_response('Invalid request body.', status_code=400)
    if not isinstance(body, dict):
        return error_response('Invalid request body.', status_code=400)

    is_active = body.get('is_active')
    if not isinstance(is_active, bool):
        return error_response('is_active must be true or false.', status_code=400)

    row = _row()
    was_active = row.is_active
    if is_active:
        message = (body.get('message') or '').strip()
        if not message:
            return error_response('message is required when maintenance is active.', status_code=400)
        try:
            resume_at = _parse_resume_at(body.get('resume_at'))
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        row.is_active = True
        row.message = message[:256]
        row.resume_at = resume_at
    else:
        row.is_active = False
        row.message = ''
        row.resume_at = None
    row.updated_by_username = request.rbac_user.username
    row.save()
    if is_active and not was_active:
        _fire_webhook(row)
    return success_response('Maintenance status updated.', data=maintenance_dict(row))
