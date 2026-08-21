import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.api_response import error_response, success_response
from hardware.services import serial_from_request, touch_from_request
from users_rbac.audit import record_event
from users_rbac.models import RbacAuditAction, RbacUser
from users_rbac.services import login as cognito_login
from users_rbac.services import refresh as cognito_refresh


@csrf_exempt
@require_POST
def login_view(request):
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("Invalid request body.", status_code=400)

    username = (body.get("email") or body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return error_response("Username or email and password are required.", status_code=400)

    local_user = (
        RbacUser.objects.filter(username__iexact=username).first()
        or RbacUser.objects.filter(email__iexact=username).first()
    )
    try:
        tokens = cognito_login(username, password)
    except ValueError as exc:
        record_event(
            request,
            action=RbacAuditAction.AUTH_LOGIN_FAILURE,
            actor=local_user,
            actor_username=username,
            detail_json={'username': username},
        )
        return error_response(str(exc), status_code=401)

    detail = {'username': username}
    serial = serial_from_request(request, body)
    if serial:
        detail['device_serial'] = serial
        touch_from_request(
            request, action='login', body=body, user=local_user,
        )

    record_event(
        request,
        action=RbacAuditAction.AUTH_LOGIN_SUCCESS,
        actor=local_user,
        target=local_user,
        actor_username=username,
        detail_json=detail,
    )
    return success_response("Signed in successfully.", data=tokens)


@csrf_exempt
@require_POST
def refresh_view(request):
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return error_response("Invalid request body.", status_code=400)

    refresh_token = (body.get("refresh_token") or "").strip()
    username = (body.get("username") or body.get("email") or "").strip()

    if not refresh_token or not username:
        return error_response("Refresh token and username are required.", status_code=400)

    try:
        tokens = cognito_refresh(refresh_token, username)
    except ValueError as exc:
        return error_response(str(exc), status_code=401)

    return success_response("Session refreshed.", data=tokens)
