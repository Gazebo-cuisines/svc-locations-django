"""Append-only RBAC audit writes and list APIs."""

from datetime import datetime, time

from django.db.models import Q, TextField
from django.db.models.functions import Cast
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from core.api_response import error_response, success_response
from product.models import ProductAudit
from stock_ledger.models import StockEntry
from users_rbac.auth import client_ip, require_admin, require_auth
from users_rbac.models import RbacAuditEvent, RbacUser

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def profile_snapshot(user: RbacUser) -> dict:
    return {
        'username': user.username,
        'email': user.email,
        'display_name': user.display_name,
        'is_active': user.is_active,
    }


def record_event(
    request,
    *,
    action: str,
    actor: RbacUser | None = None,
    target: RbacUser | None = None,
    actor_username: str | None = None,
    before_json=None,
    after_json=None,
    detail_json=None,
) -> RbacAuditEvent:
    actor = actor or getattr(request, 'rbac_user', None)
    return RbacAuditEvent.objects.create(
        action=action,
        actor_sub=actor.cognito_sub if actor else None,
        actor_username=(actor.username if actor else None) or actor_username,
        actor_display_name=actor.display_name if actor else None,
        target_user=target,
        target_username=target.username if target else None,
        target_sub=target.cognito_sub if target else None,
        request_method=request.method,
        request_path=request.path,
        request_id=request.headers.get('X-Request-Id') or request.META.get('HTTP_X_REQUEST_ID'),
        source_ip=client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT') or '',
        before_json=before_json,
        after_json=after_json,
        detail_json=detail_json,
    )


def event_dict(row: RbacAuditEvent) -> dict:
    return {
        'id': row.id,
        'at': row.at.isoformat() if row.at else None,
        'action': row.action,
        'actor_sub': row.actor_sub,
        'actor_username': row.actor_username,
        'actor_display_name': row.actor_display_name,
        'target_user_id': row.target_user_id,
        'target_username': row.target_username,
        'target_sub': row.target_sub,
        'request_method': row.request_method,
        'request_path': row.request_path,
        'request_id': row.request_id,
        'source_ip': row.source_ip,
        'user_agent': row.user_agent,
        'before_json': row.before_json,
        'after_json': row.after_json,
        'detail_json': row.detail_json,
    }


def _parse_dt(value: str, label: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'Invalid {label} datetime.') from exc


def _page_params(request):
    try:
        limit = int(request.GET.get('limit') or DEFAULT_LIMIT)
        offset = int(request.GET.get('offset') or 0)
    except ValueError as exc:
        raise ValueError('limit and offset must be integers.') from exc
    if limit < 1 or offset < 0:
        raise ValueError('limit must be >= 1 and offset >= 0.')
    return min(limit, MAX_LIMIT), offset


def _filtered_events(qs, request):
    actor = (request.GET.get('actor') or '').strip()
    target = (request.GET.get('target') or '').strip()
    action = (request.GET.get('action') or '').strip()
    if actor:
        qs = qs.filter(Q(actor_username__iexact=actor) | Q(actor_sub=actor))
    if target:
        qs = qs.filter(Q(target_username__iexact=target) | Q(target_sub=target))
    if action:
        qs = qs.filter(action=action)
    dt_from = _parse_dt((request.GET.get('from') or '').strip(), 'from')
    dt_to = _parse_dt((request.GET.get('to') or '').strip(), 'to')
    if dt_from:
        qs = qs.filter(at__gte=dt_from)
    if dt_to:
        qs = qs.filter(at__lte=dt_to)
    return qs


@csrf_exempt
@require_GET
@require_admin
def audit_list(request):
    try:
        limit, offset = _page_params(request)
        qs = _filtered_events(RbacAuditEvent.objects.all(), request)
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    count = qs.count()
    rows = qs[offset : offset + limit]
    return success_response(
        'Audit events fetched.',
        data={
            'items': [event_dict(row) for row in rows],
            'count': count,
            'limit': limit,
            'offset': offset,
        },
    )


@csrf_exempt
@require_GET
@require_admin
def user_audit(request, user_id: int):
    user = RbacUser.objects.filter(pk=user_id).first()
    if not user:
        return error_response("We couldn't find that user.", status_code=404)
    as_role = (request.GET.get('as') or 'target').strip()
    if as_role not in ('actor', 'target', 'both'):
        return error_response('as must be actor, target, or both.', status_code=400)
    actor_q = Q(actor_sub=user.cognito_sub) | Q(actor_username=user.username)
    target_q = Q(target_user=user) | Q(target_sub=user.cognito_sub)
    if as_role == 'actor':
        qs = RbacAuditEvent.objects.filter(actor_q)
    elif as_role == 'target':
        qs = RbacAuditEvent.objects.filter(target_q)
    else:
        qs = RbacAuditEvent.objects.filter(actor_q | target_q)
    try:
        limit, offset = _page_params(request)
        qs = _filtered_events(qs, request)
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    count = qs.count()
    rows = qs[offset : offset + limit]
    return success_response(
        'User audit fetched.',
        data={
            'items': [event_dict(row) for row in rows],
            'count': count,
            'limit': limit,
            'offset': offset,
        },
    )


def _in_range(at_iso, dt_from, dt_to) -> bool:
    if not at_iso:
        return not dt_from and not dt_to
    try:
        at = datetime.fromisoformat(str(at_iso).replace('Z', '+00:00'))
    except ValueError:
        return False
    if dt_from and at < dt_from:
        return False
    if dt_to and at > dt_to:
        return False
    return True


def _stock_activity_items(user: RbacUser, dt_from, dt_to) -> list[dict]:
    """Stock ledger rows stamped as this user (read-only)."""
    stock_qs = StockEntry.objects.filter(
        Q(actor_user_id=user.id) | Q(lan_username__iexact=user.username)
    ).select_related('unit', 'location', 'lot__product')
    if dt_from:
        stock_qs = stock_qs.filter(recorded_at__gte=dt_from)
    if dt_to:
        stock_qs = stock_qs.filter(recorded_at__lte=dt_to)
    items = []
    for entry in stock_qs:
        at = entry.recorded_at.isoformat() if entry.recorded_at else None
        items.append(
            {
                'source': 'stock',
                'at': at,
                'action': entry.entry_type,
                'entry_id': entry.id,
                'product_id': entry.lot.product_id,
                'product_name': entry.lot.product.name,
                'location_id': entry.location_id,
                'quantity': str(entry.quantity) if entry.quantity is not None else None,
                'lan_username': entry.lan_username,
                'actor_user_id': entry.actor_user_id,
                'source_ip': entry.source_workstation_ip,
                'request_path': None,
            }
        )
    return items


def _activity_items(user: RbacUser, dt_from, dt_to) -> list[dict]:
    items = []
    rbac_qs = RbacAuditEvent.objects.filter(
        Q(actor_sub=user.cognito_sub) | Q(actor_username=user.username)
    )
    if dt_from:
        rbac_qs = rbac_qs.filter(at__gte=dt_from)
    if dt_to:
        rbac_qs = rbac_qs.filter(at__lte=dt_to)
    for row in rbac_qs:
        item = event_dict(row)
        item['source'] = 'rbac'
        items.append(item)

    items.extend(_stock_activity_items(user, dt_from, dt_to))

    product_rows = ProductAudit.objects.annotate(
        events_text=Cast('timeline_events', TextField()),
    ).filter(events_text__contains=user.cognito_sub)
    for row in product_rows:
        for event in row.timeline_events or []:
            if event.get('actor_sub') != user.cognito_sub:
                continue
            if not _in_range(event.get('at'), dt_from, dt_to):
                continue
            items.append(
                {
                    'source': 'product',
                    'at': event.get('at'),
                    'action': event.get('action'),
                    'product_id': row.product_id,
                    'entity': event.get('entity'),
                    'actor_sub': event.get('actor_sub'),
                    'actor_name': event.get('actor_name'),
                    'source_ip': event.get('source_workstation_ip'),
                    'request_path': event.get('request_path'),
                }
            )

    items.sort(key=lambda row: row.get('at') or '', reverse=True)
    return items


def _today_range():
    day = timezone.localdate()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end = timezone.make_aware(datetime.combine(day, time.max), tz)
    return start, end


@csrf_exempt
@require_GET
@require_auth
def me_activity(request):
    """Logged-in user's stock actions only (mobile \"my day\")."""
    user = request.rbac_user
    try:
        raw_from = (request.GET.get('from') or '').strip()
        raw_to = (request.GET.get('to') or '').strip()
        if not raw_from and not raw_to:
            dt_from, dt_to = _today_range()
        else:
            dt_from = _parse_dt(raw_from, 'from')
            dt_to = _parse_dt(raw_to, 'to')
        limit, offset = _page_params(request)
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    items = _stock_activity_items(user, dt_from, dt_to)
    items.sort(key=lambda row: row.get('at') or '', reverse=True)
    count = len(items)
    return success_response(
        'My activity fetched.',
        data={
            'items': items[offset : offset + limit],
            'count': count,
            'limit': limit,
            'offset': offset,
        },
    )


@csrf_exempt
@require_GET
@require_admin
def user_activity(request, user_id: int):
    user = RbacUser.objects.filter(pk=user_id).first()
    if not user:
        return error_response("We couldn't find that user.", status_code=404)
    try:
        limit, offset = _page_params(request)
        dt_from = _parse_dt((request.GET.get('from') or '').strip(), 'from')
        dt_to = _parse_dt((request.GET.get('to') or '').strip(), 'to')
    except ValueError as exc:
        return error_response(str(exc), status_code=400)
    items = _activity_items(user, dt_from, dt_to)
    count = len(items)
    return success_response(
        'User activity fetched.',
        data={
            'items': items[offset : offset + limit],
            'count': count,
            'limit': limit,
            'offset': offset,
        },
    )
