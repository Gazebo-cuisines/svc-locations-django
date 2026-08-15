"""Upsert error tickets. Fingerprint = sha256(message + first stack line)[:32]."""

import hashlib
import json
import traceback

from django.utils import timezone

from core.models import ErrorTicket, ErrorTicketSource, ErrorTicketStatus

MSG_MAX = 1024
STACK_MAX = 8000
URL_MAX = 512
PAYLOAD_MAX = 4000


def fingerprint(message: str, stack: str) -> str:
    top = (stack or '').splitlines()[0] if stack else ''
    raw = f'{message or ""}\n{top}'
    return hashlib.sha256(raw.encode('utf-8', errors='replace')).hexdigest()[:32]


def _clip(value, n: int) -> str:
    if value is None:
        return ''
    text = value if isinstance(value, str) else str(value)
    return text[:n]


def _payload(value):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > PAYLOAD_MAX:
        return {'raw': text[:PAYLOAD_MAX]}
    if isinstance(value, (dict, list)):
        return value
    return {'raw': text}


def ticket_dict(ticket: ErrorTicket) -> dict:
    return {
        'id': ticket.id,
        'fingerprint': ticket.fingerprint,
        'status': ticket.status,
        'note': ticket.note,
        'message': ticket.message,
        'stack': ticket.stack,
        'url': ticket.url,
        'source': ticket.source,
        'occurrences': ticket.occurrences,
        'actor_username': ticket.actor_username,
        'payload': ticket.payload,
        'created_at': ticket.created_at.isoformat() if ticket.created_at else None,
        'last_seen_at': ticket.last_seen_at.isoformat() if ticket.last_seen_at else None,
    }


def record_error(
    *,
    message: str,
    stack: str = '',
    url: str = '',
    source: str = ErrorTicketSource.CLIENT,
    actor_sub: str = '',
    actor_username: str = '',
    payload=None,
) -> ErrorTicket:
    message = _clip(message, MSG_MAX) or 'Unknown error'
    stack = _clip(stack, STACK_MAX)
    url = _clip(url, URL_MAX)
    fp = fingerprint(message, stack)
    now = timezone.now()
    ticket, created = ErrorTicket.objects.get_or_create(
        fingerprint=fp,
        defaults={
            'status': ErrorTicketStatus.OPEN,
            'message': message,
            'stack': stack,
            'url': url,
            'source': source,
            'actor_sub': actor_sub or '',
            'actor_username': actor_username or '',
            'payload': _payload(payload),
            'last_seen_at': now,
        },
    )
    if created:
        return ticket
    ticket.occurrences += 1
    ticket.last_seen_at = now
    ticket.message = message
    ticket.stack = stack or ticket.stack
    ticket.url = url or ticket.url
    ticket.source = source
    if actor_username:
        ticket.actor_username = actor_username
    if actor_sub:
        ticket.actor_sub = actor_sub
    if payload is not None:
        ticket.payload = _payload(payload)
    if ticket.status == ErrorTicketStatus.RESOLVED:
        ticket.status = ErrorTicketStatus.OPEN
    ticket.save()
    return ticket


def record_exception(request, exception: BaseException) -> ErrorTicket | None:
    try:
        user = getattr(request, 'rbac_user', None)
        return record_error(
            message=str(exception) or exception.__class__.__name__,
            stack=''.join(
                traceback.format_exception(
                    type(exception), exception, exception.__traceback__
                )
            ),
            url=request.get_full_path(),
            source=ErrorTicketSource.SERVER,
            actor_sub=getattr(user, 'cognito_sub', '') or '',
            actor_username=getattr(user, 'username', '') or '',
        )
    except Exception:
        return None
