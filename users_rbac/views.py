import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.api_response import error_response, success_response
from users_rbac.services import login as cognito_login


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
        return error_response("Email and password are required.", status_code=400)

    try:
        tokens = cognito_login(username, password)
    except ValueError as exc:
        return error_response(str(exc), status_code=401)

    return success_response("Signed in successfully.", data=tokens)
