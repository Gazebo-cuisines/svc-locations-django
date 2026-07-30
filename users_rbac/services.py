"""Cognito auth helpers."""

import base64
import hashlib
import hmac
import os

import boto3
from botocore.exceptions import ClientError, ProfileNotFound


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError('Auth service is not configured.')


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
        code = exc.response["Error"]["Code"]
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            raise ValueError("Invalid email or password.") from exc
        if code == "UserNotConfirmedException":
            raise ValueError("Please confirm your account before signing in.") from exc
        if code == "PasswordResetRequiredException":
            raise ValueError("You need to reset your password before signing in.") from exc
        raise ValueError("We couldn't sign you in right now. Please try again.") from exc

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
