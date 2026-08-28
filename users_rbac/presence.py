from datetime import timedelta

from django.utils import timezone

from users_rbac.models import RbacUser

STAMP_AFTER = timedelta(seconds=60)
ONLINE_AFTER = timedelta(minutes=2)
IDLE_AFTER = timedelta(minutes=10)


def stamp_presence(user: RbacUser, *, ip: str, user_agent: str):
    now = timezone.now()
    if user.last_seen_at and now - user.last_seen_at < STAMP_AFTER:
        return
    ip_value = (ip or '')[:45] or None
    ua_value = (user_agent or '')[:256]
    RbacUser.objects.filter(pk=user.pk).update(
        last_seen_at=now,
        last_ip=ip_value,
        last_user_agent=ua_value,
    )
    user.last_seen_at = now
    user.last_ip = ip_value
    user.last_user_agent = ua_value


def presence_for(user: RbacUser) -> str:
    if not user.last_seen_at:
        return 'offline'
    age = timezone.now() - user.last_seen_at
    if age < ONLINE_AFTER:
        return 'online'
    if age < IDLE_AFTER:
        return 'idle'
    return 'offline'


def presence_dict(user: RbacUser) -> dict:
    return {
        'id': user.id,
        'username': user.username,
        'display_name': user.display_name,
        'presence': presence_for(user),
        'last_seen_at': user.last_seen_at.isoformat() if user.last_seen_at else None,
        'last_ip': user.last_ip,
        'last_user_agent': user.last_user_agent or '',
    }
