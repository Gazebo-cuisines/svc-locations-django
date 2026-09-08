"""Chat endpoint for the Bedrock agent plus the action group schema."""

import json
import logging

from botocore.exceptions import BotoCoreError, ClientError
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.ai_tools import openapi_schema
from core.api_response import error_response, success_response
from core.bedrock import handle_chat
from core.models import AiCase
from users_rbac.auth import require_auth

logger = logging.getLogger(__name__)


def _actor(user):
    if user is None:
        return None, '', ''
    name = (getattr(user, 'display_name', '') or '').strip() or user.username
    return user.id, user.username, name


def record_turn(*, session_id: str, user, message: str, result: dict) -> None:
    findings = result.get('findings') or {}
    product = findings.get('product') or {}
    bag = findings.get('bag') or {}
    uid, username, name = _actor(user)
    turn = {
        'at': timezone.now().isoformat(),
        'user_id': uid,
        'username': username,
        'display_name': name,
        'message': message,
        'answer': result.get('answer') or '',
        'tool_calls': result.get('tool_calls') or [],
        'product': product or None,
        'bag': bag or None,
        'decision': findings.get('decision'),
        'briefing': findings.get('briefing'),
        'goods_in': findings.get('goods_in'),
        'goods_out': findings.get('goods_out'),
        'cancelled': findings.get('cancelled'),
        'reversals': findings.get('reversals'),
        'balances': findings.get('balances'),
    }
    case, _ = AiCase.objects.get_or_create(
        session_id=session_id,
        defaults={
            'opened_by_user_id': uid,
            'opened_by_username': username,
            'opened_by_name': name,
            'turns': [],
        },
    )
    turns = list(case.turns or [])
    turns.append(turn)
    case.turns = turns
    if product.get('product_id'):
        case.product_id = product['product_id']
    if product.get('recipe_code'):
        case.recipe_code = product['recipe_code']
    if bag.get('entry_code'):
        case.bag_code = bag['entry_code']
    case.save()


def case_dict(case: AiCase) -> dict:
    return {
        'session_id': case.session_id,
        'opened_by_user_id': case.opened_by_user_id,
        'opened_by_username': case.opened_by_username,
        'opened_by_name': case.opened_by_name,
        'product_id': case.product_id,
        'recipe_code': case.recipe_code,
        'bag_code': case.bag_code,
        'turn_count': len(case.turns or []),
        'turns': case.turns or [],
        'created_at': case.created_at.isoformat() if case.created_at else None,
        'updated_at': case.updated_at.isoformat() if case.updated_at else None,
    }


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
        result = handle_chat(
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
    try:
        record_turn(
            session_id=result['session_id'],
            user=getattr(request, 'rbac_user', None),
            message=message,
            result=result,
        )
    except Exception:
        logger.exception('ai case save failed')
    return success_response('Assistant replied.', result)


@csrf_exempt
@require_GET
@require_auth
def ai_cases_api(request):
    """List recent chat cases (JSON turns)."""
    qs = AiCase.objects.all()
    session_id = (request.GET.get('session_id') or '').strip()
    if session_id:
        qs = qs.filter(session_id=session_id)
    product_id = (request.GET.get('product_id') or '').strip()
    if product_id:
        qs = qs.filter(product_id=product_id)
    username = (request.GET.get('username') or '').strip()
    if username:
        qs = qs.filter(opened_by_username=username)
    rows = [case_dict(case) for case in qs[:50]]
    return success_response('AI cases fetched.', rows)


@csrf_exempt
@require_GET
@require_auth
def ai_case_detail_api(request, session_id):
    """Pull one session JSON."""
    case = AiCase.objects.filter(session_id=session_id).first()
    if case is None:
        return error_response('Case not found.', status_code=404)
    return success_response('AI case fetched.', case_dict(case))


@csrf_exempt
@require_GET
@require_auth
def ai_tools_schema_api(request):
    """OpenAPI document for the Bedrock agent action group."""
    return success_response('Action group schema fetched.', openapi_schema())
