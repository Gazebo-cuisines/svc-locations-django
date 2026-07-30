import base64
import json
from datetime import datetime, timezone

from product.models import ProductAudit, ProductAuditAction


def _decode_bearer_claims(request) -> dict:
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return {}
    token = auth_header.split(' ', 1)[1].strip()
    parts = token.split('.')
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padding = '=' * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding).decode('utf-8')
        claims = json.loads(decoded)
        return claims if isinstance(claims, dict) else {}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}


def _changed_fields(before_data, after_data) -> list[str]:
    before = before_data or {}
    after = after_data or {}
    keys = set(before.keys()) | set(after.keys())
    return sorted([key for key in keys if before.get(key) != after.get(key)])


def capture_product_audit(
    request,
    *,
    product_id: int,
    entity: str,
    action: str,
    before_data,
    after_data,
):
    if action not in ProductAuditAction.values:
        return

    claims = _decode_bearer_claims(request)
    actor_name = (
        claims.get('name')
        or claims.get('cognito:username')
        or claims.get('username')
    )

    try:
        row, _ = ProductAudit.objects.get_or_create(
            product_id=product_id,
        )
        events = list(row.timeline_events or [])
        events.append({
            'at': datetime.now(timezone.utc).isoformat(),
            'entity': entity,
            'action': action,
            'actor_sub': claims.get('sub'),
            'actor_name': actor_name,
            'actor_email': claims.get('email'),
            'request_method': request.method,
            'request_path': request.path,
            'source_workstation_ip': request.META.get('REMOTE_ADDR'),
            'source_workstation': request.META.get('HTTP_USER_AGENT'),
            'before_json': before_data,
            'after_json': after_data,
            'changed_fields': _changed_fields(before_data, after_data),
        })
        row.timeline_events = events
        row.actor_sub = claims.get('sub')
        row.lan_username = actor_name
        row.actor_email = claims.get('email')
        row.source_workstation_ip = request.META.get('REMOTE_ADDR')
        row.source_workstation = request.META.get('HTTP_USER_AGENT')
        row.save(
            update_fields=[
                'timeline_events',
                'actor_sub',
                'lan_username',
                'actor_email',
                'source_workstation_ip',
                'source_workstation',
                'updated_at',
            ],
        )
    except Exception:
        # Silent audit failures: business request must still succeed.
        return
