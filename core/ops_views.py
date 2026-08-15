import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.api_response import error_response, success_response
from core.models import ErrorTicket, ErrorTicketSource, ErrorTicketStatus
from core.ops import record_error, ticket_dict
from users_rbac.auth import require_admin, require_auth
from users_rbac.permissions import require_any_admin

_STATUSES = {choice.value for choice in ErrorTicketStatus}


def _parse_body(request):
    try:
        body = json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


@csrf_exempt
@require_http_methods(['GET', 'POST'])
@require_auth
def errors_collection(request):
    if request.method == 'GET':
        denied = require_any_admin(request)
        if denied:
            return denied
        qs = ErrorTicket.objects.all()
        status = (request.GET.get('status') or '').strip()
        if status:
            if status not in _STATUSES:
                return error_response('Status must be open, investigating, or resolved.')
            qs = qs.filter(status=status)
        rows = [ticket_dict(ticket) for ticket in qs[:100]]
        return success_response('Error tickets fetched.', data=rows)

    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.')
    message = (body.get('message') or '').strip()
    if not message:
        return error_response('Message is required.')
    user = request.rbac_user
    ticket = record_error(
        message=message,
        stack=body.get('stack') or '',
        url=body.get('url') or '',
        source=ErrorTicketSource.CLIENT,
        actor_sub=user.cognito_sub,
        actor_username=user.username,
        payload=body.get('payload'),
    )
    created = ticket.occurrences == 1
    return success_response(
        'Error recorded.',
        data=ticket_dict(ticket),
        status_code=201 if created else 200,
    )


@csrf_exempt
@require_http_methods(['PATCH'])
@require_admin
def error_detail(request, pk):
    ticket = ErrorTicket.objects.filter(pk=pk).first()
    if ticket is None:
        return error_response("We couldn't find that error ticket.", status_code=404)
    body = _parse_body(request)
    if body is None:
        return error_response('Invalid request body.')
    if 'status' in body:
        status = (body.get('status') or '').strip()
        if status not in _STATUSES:
            return error_response('Status must be open, investigating, or resolved.')
        ticket.status = status
    if 'note' in body:
        note = body.get('note')
        if note is None:
            note = ''
        if not isinstance(note, str):
            return error_response('Note must be text.')
        ticket.note = note
    ticket.save()
    return success_response('Error ticket updated.', data=ticket_dict(ticket))
