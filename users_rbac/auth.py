"""Cognito JWT verification and request auth."""

import os
import ssl
from functools import wraps

import certifi
import jwt

from core.api_response import error_response
from users_rbac.models import RbacUser

_jwks_client = None


def client_ip(request) -> str:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR') or ''
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or ''


def _issuer() -> str:
    pool = os.getenv('COGNITO_USER_POOL_ID')
    if not pool:
        raise ValueError('Auth service is not configured.')
    region = os.getenv('COGNITO_REGION', 'eu-west-2')
    return f'https://cognito-idp.{region}.amazonaws.com/{pool}'


def _jwks_url() -> str:
    return f'{_issuer()}/.well-known/jwks.json'


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        ctx = ssl.create_default_context(cafile=certifi.where())
        _jwks_client = jwt.PyJWKClient(
            _jwks_url(),
            cache_jwk_set=True,
            ssl_context=ctx,
        )
    return _jwks_client


def _get_signing_key(token: str):
    return _get_jwks_client().get_signing_key_from_jwt(token).key


def _client_ids() -> list[str]:
    """Accept one or more app clients (web + mobile)."""
    ids: list[str] = []
    multi = os.getenv('COGNITO_CLIENT_IDS') or ''
    for part in multi.split(','):
        value = part.strip()
        if value and value not in ids:
            ids.append(value)
    single = (os.getenv('COGNITO_CLIENT_ID') or '').strip()
    if single and single not in ids:
        ids.append(single)
    return ids


def _audience_ok(claims: dict, client_ids: list[str]) -> bool:
    if not client_ids:
        return False
    token_use = claims.get('token_use')
    if token_use == 'id':
        aud = claims.get('aud')
        if isinstance(aud, list):
            return any(client_id in aud for client_id in client_ids)
        return aud in client_ids
    if token_use == 'access':
        return claims.get('client_id') in client_ids
    return False


def verify_token(token: str) -> dict:
    client_ids = _client_ids()
    if not client_ids or not os.getenv('COGNITO_USER_POOL_ID'):
        raise ValueError('Auth service is not configured.')
    try:
        claims = jwt.decode(
            token,
            _get_signing_key(token),
            algorithms=['RS256'],
            issuer=_issuer(),
            options={'verify_aud': False, 'require': ['exp', 'iss', 'sub']},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ValueError('expired') from exc
    except Exception as exc:
        raise ValueError('invalid') from exc
    if not _audience_ok(claims, client_ids):
        raise ValueError('invalid')
    return claims


def attach_user(request, *, missing='error', invalid='error'):
    """Set request.rbac_user from Bearer JWT. missing/invalid: 'error' | 'ok'."""
    if getattr(request, 'rbac_user', None):
        return None
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        if missing == 'error':
            return error_response('Please sign in to continue.', status_code=401)
        return None
    token = header.split(' ', 1)[1].strip()
    try:
        claims = verify_token(token)
    except ValueError:
        if invalid == 'error':
            return error_response(
                'Your session is not valid. Please sign in again.',
                status_code=401,
            )
        return None
    sub = claims.get('sub')
    if not sub:
        if invalid == 'error':
            return error_response(
                'Your session is not valid. Please sign in again.',
                status_code=401,
            )
        return None
    try:
        user = RbacUser.objects.get(cognito_sub=sub)
    except RbacUser.DoesNotExist:
        if invalid == 'error':
            return error_response("We couldn't find your account.", status_code=401)
        return None
    if not user.is_active:
        if missing == 'error' or invalid == 'error':
            return error_response('This account is disabled.', status_code=403)
        return None
    request.rbac_user = user
    request.cognito_claims = claims
    request.client_ip = client_ip(request)
    request.user_agent = request.META.get('HTTP_USER_AGENT') or ''
    return None


def require_auth(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        denied = attach_user(request)
        if denied:
            return denied
        return view_func(request, *args, **kwargs)

    return wrapped


def require_admin(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        # Circular: permissions → audit → auth.require_admin
        from users_rbac.permissions import require_any_admin

        denied = require_any_admin(request)
        if denied:
            return denied
        return view_func(request, *args, **kwargs)

    return require_auth(wrapped)
