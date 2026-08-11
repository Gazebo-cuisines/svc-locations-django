"""Append-only location audit (who / when / before / after)."""

from locations.models import Location, LocationAuditAction, LocationAuditEvent
from locations.presentation import location_detail_dict
from users_rbac.auth import attach_user, client_ip


def audit_snapshot(location: Location) -> dict:
    data = location_detail_dict(location)
    data.pop('image_url', None)  # ephemeral presign — keep image_key only
    return data


def _changed_fields(before_data, after_data) -> list[str] | None:
    if before_data is None or after_data is None:
        return None
    keys = set(before_data) | set(after_data)
    return sorted(k for k in keys if before_data.get(k) != after_data.get(k))


def _actor_stamp(request) -> dict:
    attach_user(request, missing='ok', invalid='ok')
    user = getattr(request, 'rbac_user', None)
    if user:
        return {
            'actor_sub': user.cognito_sub,
            'actor_username': user.username,
            'actor_display_name': user.display_name or user.username,
            'actor_email': user.email,
        }
    # Optional FE headers when container uses X-API-Token (no Cognito Bearer)
    return {
        'actor_sub': request.headers.get('X-Actor-Sub') or None,
        'actor_username': request.headers.get('X-Actor-Username') or None,
        'actor_display_name': request.headers.get('X-Actor-Name') or None,
        'actor_email': request.headers.get('X-Actor-Email') or None,
    }


def capture_location_audit(
    request,
    *,
    location_id: int,
    action: str,
    before_data=None,
    after_data=None,
    location_name: str | None = None,
):
    if action not in LocationAuditAction.values:
        return
    try:
        stamp = _actor_stamp(request)
        name = location_name
        if name is None and isinstance(after_data, dict):
            name = after_data.get('name')
        if name is None and isinstance(before_data, dict):
            name = before_data.get('name')
        LocationAuditEvent.objects.create(
            action=action,
            location_id=location_id,
            location_name=name,
            actor_sub=stamp['actor_sub'],
            actor_username=stamp['actor_username'],
            actor_display_name=stamp['actor_display_name'],
            actor_email=stamp['actor_email'],
            request_method=request.method,
            request_path=request.path,
            source_ip=client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT') or '',
            before_json=before_data,
            after_json=after_data,
            changed_fields=_changed_fields(before_data, after_data),
        )
    except Exception:
        # Silent audit failures: business request must still succeed.
        return


def event_dict(row: LocationAuditEvent) -> dict:
    return {
        'id': row.id,
        'at': row.at.isoformat() if row.at else None,
        'action': row.action,
        'location_id': row.location_id,
        'location_name': row.location_name,
        'actor_sub': row.actor_sub,
        'actor_username': row.actor_username,
        'actor_display_name': row.actor_display_name,
        'actor_email': row.actor_email,
        'request_method': row.request_method,
        'request_path': row.request_path,
        'source_ip': row.source_ip,
        'user_agent': row.user_agent,
        'before_json': row.before_json,
        'after_json': row.after_json,
        'changed_fields': row.changed_fields,
    }
