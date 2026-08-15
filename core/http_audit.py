"""Best-effort S3 log of mutating API request/response JSON. Never raises."""

import json
import os
import uuid
from datetime import datetime, timezone as dt_timezone
from threading import Thread

import boto3
from django.conf import settings

from users_rbac.auth import client_ip

REDACT_KEYS = {
    'password',
    'refresh_token',
    'access_token',
    'id_token',
    'client_secret',
    'token',
    'authorization',
}
BODY_MAX = 64 * 1024
SKIP_PREFIXES = ('/static/', '/favicon')


def _s3_client():
    profile = os.getenv('AWS_PROFILE') or getattr(settings, 'AWS_PROFILE', None)
    region = (
        os.getenv('AWS_DEFAULT_REGION')
        or getattr(settings, 'AWS_DEFAULT_REGION', None)
        or 'eu-west-2'
    )
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    except Exception:
        session = boto3.Session()
    return session.client('s3', region_name=region)


def _bucket() -> str:
    return getattr(settings, 'AUDIT_S3_BUCKET', None) or 'gazebo-audit-logging'


def _redact(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in REDACT_KEYS:
                out[key] = '[redacted]'
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _parse_json(raw: bytes):
    if not raw:
        return None
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return {'_skipped': 'binary'}
    if len(text) > BODY_MAX:
        return {'_truncated': True, '_raw': text[:BODY_MAX]}
    try:
        return _redact(json.loads(text))
    except json.JSONDecodeError:
        return {'_raw': text[:BODY_MAX]}


def _clip_json(value):
    if value is None:
        return None
    text = json.dumps(value, default=str)
    if len(text) <= BODY_MAX:
        return value
    return {'_truncated': True, '_raw': text[:BODY_MAX]}


def request_json(request):
    content_type = (request.META.get('CONTENT_TYPE') or '').lower()
    if 'multipart/' in content_type:
        return {'_skipped': 'multipart'}
    try:
        return _parse_json(request.body)
    except Exception:
        return {'_skipped': 'unreadable'}


def response_json(response):
    if response is None:
        return None
    content_type = (getattr(response, 'content_type', None) or '').lower()
    if content_type and 'json' not in content_type:
        return {'_skipped': 'non-json', 'content_type': content_type}
    try:
        content = response.content
    except Exception:
        return {'_skipped': 'streaming'}
    return _parse_json(content)


def build_payload(request, response):
    user = getattr(request, 'rbac_user', None)
    return {
        'at': datetime.now(dt_timezone.utc).isoformat().replace('+00:00', 'Z'),
        'method': request.method,
        'path': request.path,
        'query': request.META.get('QUERY_STRING') or '',
        'status': None if response is None else response.status_code,
        'actor_username': getattr(user, 'username', '') or '',
        'actor_sub': getattr(user, 'cognito_sub', '') or '',
        'ip': client_ip(request),
        'user_agent': request.META.get('HTTP_USER_AGENT') or '',
        'in': _clip_json(request_json(request)),
        'out': _clip_json(response_json(response)),
    }


def _put(payload: dict):
    try:
        now = datetime.now(dt_timezone.utc)
        path = (payload.get('path') or '').strip('/').replace('/', '_')[:80] or 'root'
        key = (
            f'api-http/{now.strftime("%Y/%m/%d")}/'
            f'{now.strftime("%H%M%S")}-{uuid.uuid4().hex[:8]}'
            f'-{payload.get("method")}-{path}.json'
        )
        body = json.dumps(payload, default=str).encode('utf-8')
        _s3_client().put_object(
            Bucket=_bucket(),
            Key=key,
            Body=body,
            ContentType='application/json',
            ServerSideEncryption='AES256',
        )
    except Exception:
        return


def _start_audit(payload: dict):
    # ponytail: daemon thread; sync put if 5-user latency is fine
    Thread(target=_put, args=(payload,), daemon=True).start()


def audit_request(request, response):
    try:
        if (request.path or '').startswith(SKIP_PREFIXES):
            return
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        _start_audit(build_payload(request, response))
    except Exception:
        return
