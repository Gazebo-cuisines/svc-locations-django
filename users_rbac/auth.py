"""Cognito JWT verification and request auth."""

import os
from functools import wraps

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
        _jwks_client = jwt.PyJWKClient(_jwks_url(), cache_jwk_set=True)
    return _jwks_client


def _get_signing_key(token: str):
    return _get_jwks_client().get_signing_key_from_jwt(token).key


def _audience_ok(claims: dict, client_id: str) -> bool:
    token_use = claims.get('token_use')
    if token_use == 'id':
        aud = claims.get('aud')
        return aud == client_id or (isinstance(aud, list) and client_id in aud)
    if token_use == 'access':
        return claims.get('client_id') == client_id
    return False


def verify_token(token: str) -> dict:
    client_id = os.getenv('COGNITO_CLIENT_ID')
    if not client_id or not os.getenv('COGNITO_USER_POOL_ID'):
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
    except jwt.InvalidTokenError as exc:
        raise ValueError('invalid') from exc
    if not _audience_ok(claims, client_id):
        raise ValueError('invalid')
    return claims


def require_auth(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer '):
            return error_response('Please sign in to continue.', status_code=401)
        token = header.split(' ', 1)[1].strip()
        try:
            claims = verify_token(token)
        except ValueError:
            return error_response(
                'Your session is not valid. Please sign in again.',
                status_code=401,
            )
        sub = claims.get('sub')
        if not sub:
            return error_response(
                'Your session is not valid. Please sign in again.',
                status_code=401,
            )
        try:
            user = RbacUser.objects.get(cognito_sub=sub)
        except RbacUser.DoesNotExist:
            return error_response("We couldn't find your account.", status_code=401)
        if not user.is_active:
            return error_response('This account is disabled.', status_code=403)
        request.rbac_user = user
        request.cognito_claims = claims
        request.client_ip = client_ip(request)
        request.user_agent = request.META.get('HTTP_USER_AGENT') or ''
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
