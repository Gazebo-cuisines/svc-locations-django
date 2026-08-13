"""Cognito auth helpers."""

import base64
import hashlib
import hmac
import os

import boto3
from botocore.exceptions import ClientError, ProfileNotFound
from django.db import IntegrityError

from users_rbac.models import RbacUser


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError('Auth service is not configured.')


def _pool_id() -> str:
    return _require_env('COGNITO_USER_POOL_ID')


def _secret_hash(username: str) -> str:
    msg = (username + _require_env("COGNITO_CLIENT_ID")).encode()
    key = _require_env("COGNITO_CLIENT_SECRET").encode()
    dig = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.b64encode(dig).decode()


def _client():
    profile = os.environ.get("AWS_PROFILE") or None
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    except ProfileNotFound:
        session = boto3.Session()  # fall back to default credentials
    return session.client(
        "cognito-idp",
        region_name=os.environ.get("COGNITO_REGION", "eu-west-2"),
    )


def _sub_from_attrs(attrs: list) -> str | None:
    for attr in attrs or []:
        if attr.get('Name') == 'sub':
            return attr.get('Value')
    return None


def _cognito_message(exc: ClientError, *, for_login: bool = False) -> str:
    code = exc.response['Error']['Code']
    if for_login and code in ('NotAuthorizedException', 'UserNotFoundException'):
        return 'Invalid username or password.'
    if code == 'UserNotFoundException':
        return "We couldn't find that user."
    if code == 'UsernameExistsException':
        return 'That username is already in use.'
    if code in ('AliasExistsException', 'InvalidParameterException'):
        return 'That username or email could not be used.'
    if code == 'InvalidPasswordException':
        return "That password doesn't meet the requirements."
    if code == 'UserNotConfirmedException':
        return 'Please confirm your account before signing in.'
    if code == 'PasswordResetRequiredException':
        return 'You need to reset your password before signing in.'
    return "We couldn't complete that request. Please try again."


def _delete_cognito_user(username: str) -> None:
    try:
        _client().admin_delete_user(UserPoolId=_pool_id(), Username=username)
    except ClientError:
        pass


def login(username: str, password: str) -> dict:
    """
    USER_PASSWORD_AUTH against Cognito.
    Returns token payload or raises ValueError with a user-facing message.
    """
    try:
        resp = _client().initiate_auth(
            ClientId=_require_env("COGNITO_CLIENT_ID"),
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
                "SECRET_HASH": _secret_hash(username),
            },
        )
    except ProfileNotFound as exc:
        raise ValueError(
            "AWS profile not found. Unset AWS_PROFILE or configure it with aws configure."
        ) from exc
    except ClientError as exc:
        raise ValueError(_cognito_message(exc, for_login=True)) from exc

    challenge = resp.get("ChallengeName")
    if challenge:
        # ponytail: password login only; wire NEW_PASSWORD_REQUIRED later if needed
        raise ValueError("Additional sign-in steps are required. Please contact support.")

    result = resp["AuthenticationResult"]
    return {
        "access_token": result["AccessToken"],
        "id_token": result["IdToken"],
        "refresh_token": result.get("RefreshToken"),
        "expires_in": result.get("ExpiresIn"),
        "token_type": result.get("TokenType", "Bearer"),
    }


def refresh(refresh_token: str, username: str) -> dict:
    """
    REFRESH_TOKEN_AUTH against Cognito (needs SECRET_HASH — browser cannot do this).
    """
    refresh_token = (refresh_token or '').strip()
    username = (username or '').strip()
    if not refresh_token or not username:
        raise ValueError('Refresh token and username are required.')

    try:
        resp = _client().initiate_auth(
            ClientId=_require_env("COGNITO_CLIENT_ID"),
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={
                "REFRESH_TOKEN": refresh_token,
                "USERNAME": username,
                "SECRET_HASH": _secret_hash(username),
            },
        )
    except ProfileNotFound as exc:
        raise ValueError(
            "AWS profile not found. Unset AWS_PROFILE or configure it with aws configure."
        ) from exc
    except ClientError as exc:
        raise ValueError(_cognito_message(exc)) from exc

    result = resp["AuthenticationResult"]
    return {
        "access_token": result["AccessToken"],
        "id_token": result["IdToken"],
        # Cognito often omits RefreshToken on refresh — caller should keep the old one.
        "refresh_token": result.get("RefreshToken") or refresh_token,
        "expires_in": result.get("ExpiresIn"),
        "token_type": result.get("TokenType", "Bearer"),
    }


def create_identity(
    username: str,
    password: str,
    *,
    email: str | None = None,
    display_name: str = '',
    created_by_sub: str | None = None,
) -> RbacUser:
    username = (username or '').strip()
    email = (email or '').strip() or None
    display_name = (display_name or '').strip()
    if not username or not password:
        raise ValueError('Username and password are required.')

    client = _client()
    pool_id = _pool_id()
    attrs = []
    if email:
        attrs = [
            {'Name': 'email', 'Value': email},
            {'Name': 'email_verified', 'Value': 'true'},
        ]
    created_in_cognito = False
    try:
        created = client.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            MessageAction='SUPPRESS',
            UserAttributes=attrs,
        )
        created_in_cognito = True
        client.admin_set_user_password(
            UserPoolId=pool_id,
            Username=username,
            Password=password,
            Permanent=True,
        )
        sub = _sub_from_attrs((created.get('User') or {}).get('Attributes'))
        if not sub:
            got = client.admin_get_user(UserPoolId=pool_id, Username=username)
            sub = _sub_from_attrs(got.get('UserAttributes'))
        if not sub:
            raise ValueError("We couldn't create that user. Please try again.")
        return RbacUser.objects.create(
            cognito_sub=sub,
            username=username,
            email=email,
            display_name=display_name,
            created_by_sub=created_by_sub,
        )
    except ClientError as exc:
        if created_in_cognito:
            _delete_cognito_user(username)
        raise ValueError(_cognito_message(exc)) from exc
    except IntegrityError as exc:
        _delete_cognito_user(username)
        raise ValueError('That username or email is already in use.') from exc
    except Exception:
        if created_in_cognito:
            _delete_cognito_user(username)
        raise


def set_active(user: RbacUser, is_active: bool) -> RbacUser:
    try:
        if is_active:
            _client().admin_enable_user(UserPoolId=_pool_id(), Username=user.username)
        else:
            _client().admin_disable_user(UserPoolId=_pool_id(), Username=user.username)
    except ClientError as exc:
        raise ValueError(_cognito_message(exc)) from exc
    user.is_active = is_active
    user.save(update_fields=['is_active', 'updated_at'])
    return user


def reset_password(user: RbacUser, password: str) -> None:
    if not password:
        raise ValueError('Password is required.')
    try:
        _client().admin_set_user_password(
            UserPoolId=_pool_id(),
            Username=user.username,
            Password=password,
            Permanent=True,
        )
    except ClientError as exc:
        raise ValueError(_cognito_message(exc)) from exc
