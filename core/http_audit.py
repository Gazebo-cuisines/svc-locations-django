"""S3 log of mutating API request/response JSON. Never raises to the user.

Request thread writes a local file (kept until S3 accepts it).
One background worker uploads those files. Slow S3 does not drop logs.
"""

import json
import logging
import tempfile
import time
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from queue import Queue
from threading import Lock, Thread

from django.conf import settings

from core.s3 import s3_client
from users_rbac.auth import client_ip

logger = logging.getLogger('core.http_audit')

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
_QUEUE: Queue = Queue()
_WORKER_LOCK = Lock()
_WORKER_STARTED = False
_RETRY_SECONDS = 2


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


def _spill_dir() -> Path:
    raw = getattr(settings, 'AUDIT_LOCAL_DIR', None)
    root = Path(raw) if raw else Path(tempfile.gettempdir()) / 'gazebo-http-audit'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _put(payload: dict) -> None:
    now = datetime.now(dt_timezone.utc)
    path = (payload.get('path') or '').strip('/').replace('/', '_')[:80] or 'root'
    key = (
        f'api-http/{now.strftime("%Y/%m/%d")}/'
        f'{now.strftime("%H%M%S")}-{uuid.uuid4().hex[:8]}'
        f'-{payload.get("method")}-{path}.json'
    )
    body = json.dumps(payload, default=str).encode('utf-8')
    s3_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=body,
        ContentType='application/json',
        ServerSideEncryption='AES256',
    )


def _upload_file(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        payload = json.loads(path.read_text())
        _put(payload)
        path.unlink(missing_ok=True)
        return True
    except Exception:
        logger.exception(
            'audit S3 put failed path=%s — keeping local file for retry',
            path,
        )
        return False


def _worker() -> None:
    while True:
        path = _QUEUE.get()
        try:
            if not _upload_file(path):
                time.sleep(_RETRY_SECONDS)
                if path.exists():
                    _QUEUE.put(path)
        finally:
            _QUEUE.task_done()


def _ensure_worker() -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        for leftover in sorted(_spill_dir().glob('*.json')):
            _QUEUE.put(leftover)
        Thread(target=_worker, name='http-audit-s3', daemon=True).start()
        _WORKER_STARTED = True


def _start_audit(payload: dict):
    path = _spill_dir() / f'{uuid.uuid4().hex}.json'
    path.write_text(json.dumps(payload, default=str))
    _ensure_worker()
    _QUEUE.put(path)


def _log_client_error(request, response) -> None:
    """Mirror 4xx/5xx API messages into journalctl (S3 already has full bodies)."""
    if response is None or response.status_code < 400:
        return
    msg = ''
    try:
        out = response_json(response)
        if isinstance(out, dict):
            msg = str(out.get('message') or '')
    except Exception:
        msg = ''
    logger.warning(
        '%s %s -> %s %s',
        request.method,
        request.path,
        response.status_code,
        msg,
    )


def audit_request(request, response):
    try:
        if (request.path or '').startswith(SKIP_PREFIXES):
            return
        _log_client_error(request, response)
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        _start_audit(build_payload(request, response))
    except Exception:
        return
