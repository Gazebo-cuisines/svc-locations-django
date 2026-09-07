"""Chat endpoint for the Bedrock agent plus the action group schema."""

import json

from botocore.exceptions import BotoCoreError, ClientError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.ai_tools import openapi_schema
from core.api_response import error_response, success_response
from core.bedrock import invoke_agent
from users_rbac.auth import require_auth


@csrf_exempt
@require_POST
@require_auth
def ai_chat_api(request):
    """POST {"message": "...", "session_id": "..."} -> agent answer."""
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return error_response('Send a valid JSON body.')
    message = str(payload.get('message') or '').strip()
    if not message:
        return error_response('Type a question for the assistant.')

    try:
        result = invoke_agent(
            message,
            session_id=str(payload.get('session_id') or ''),
            auth_header=request.headers.get('Authorization', ''),
        )
    except RuntimeError as exc:
        return error_response(str(exc), status_code=503)
    except (BotoCoreError, ClientError):
        return error_response(
            'The assistant is unavailable right now. Please try again.',
            status_code=502,
        )
    return success_response('Assistant replied.', result)


@csrf_exempt
@require_GET
@require_auth
def ai_tools_schema_api(request):
    """OpenAPI document for the Bedrock agent action group."""
    return success_response('Action group schema fetched.', openapi_schema())
