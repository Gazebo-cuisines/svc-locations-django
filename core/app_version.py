import hmac
import json

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.api_response import error_response, success_response
from core.models import AppVersion

PLATFORMS = {'android'}
DEFAULT_MESSAGE = 'Hand this device to IT to install the update.'


def _payload(platform: str) -> dict:
    row = AppVersion.objects.filter(platform=platform).first()
    min_version = (
        row.min_version
        if row
        else getattr(settings, 'APP_MIN_VERSION_ANDROID', '1.0.1')
    )
    latest = (
        (row.latest_version if row else '')
        or getattr(settings, 'APP_LATEST_VERSION_ANDROID', '')
        or min_version
    )
    message = (
        (row.message if row else '')
        or getattr(settings, 'APP_UPDATE_MESSAGE', DEFAULT_MESSAGE)
        or DEFAULT_MESSAGE
    )
    return {
        'min_version': min_version,
        'latest_version': latest,
        'message': message,
    }


def _token_denied(request):
    expected = (getattr(settings, 'APP_VERSION_API_TOKEN', None) or '').strip()
    auth = request.headers.get('Authorization') or request.META.get('HTTP_AUTHORIZATION') or ''
    got = auth.split(' ', 1)[1].strip() if auth.lower().startswith('bearer ') else ''
    if not expected or len(got) != len(expected) or not hmac.compare_digest(got, expected):
        return error_response('Not signed in.', status_code=401)
    return None


@csrf_exempt
@require_http_methods(['GET', 'PUT'])
def app_version(request):
    if request.method == 'GET':
        platform = (request.GET.get('platform') or 'android').strip().lower()
        if platform not in PLATFORMS:
            return error_response('Unknown platform.', status_code=400)
        return success_response('ok', data=_payload(platform))

    denied = _token_denied(request)
    if denied:
        return denied
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return error_response('Invalid request body.', status_code=400)
    if not isinstance(body, dict):
        return error_response('Invalid request body.', status_code=400)
    platform = str(body.get('platform') or 'android').strip().lower()
    if platform not in PLATFORMS:
        return error_response('Unknown platform.', status_code=400)
    min_version = str(body.get('min_version') or '').strip()
    if not min_version:
        return error_response('min_version is required.', status_code=400)
    latest = str(body.get('latest_version') or '').strip() or min_version
    message = str(body.get('message') or '').strip()
    defaults = {
        'min_version': min_version[:32],
        'latest_version': latest[:32],
    }
    if message:
        defaults['message'] = message[:256]
    AppVersion.objects.update_or_create(platform=platform, defaults=defaults)
    return success_response('ok', data=_payload(platform))
