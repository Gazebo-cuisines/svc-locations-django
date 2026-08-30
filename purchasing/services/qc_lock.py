from datetime import timedelta

from django.utils import timezone

from purchasing.serialize import rbac_names


LOCK_TTL = timedelta(minutes=2)


class QcLockError(ValueError):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def lock_holder(row, now=None) -> int | None:
    now = now or timezone.now()
    if not row.editor_user_id or not row.editor_heartbeat_at:
        return None
    if now - row.editor_heartbeat_at >= LOCK_TTL:
        return None
    return row.editor_user_id


def lock_info(row) -> dict | None:
    now = timezone.now()
    holder = lock_holder(row, now)
    if holder is None:
        return None
    name = rbac_names({holder}).get(holder)
    return {
        'editor_user_id': holder,
        'editor_name': name,
        'lock_expires_at': (row.editor_heartbeat_at + LOCK_TTL).isoformat(),
    }


def claim_lock(row, user_id: int | None, *, noun: str) -> None:
    if user_id in (None, ''):
        return
    user_id = int(user_id)
    holder = lock_holder(row)
    if holder is not None and holder != user_id:
        name = rbac_names({holder}).get(holder) or f'User {holder}'
        raise QcLockError(f'{name} is working on this {noun}.')
    row.editor_user_id = user_id
    row.editor_heartbeat_at = timezone.now()
    row.save(update_fields=['editor_user_id', 'editor_heartbeat_at', 'updated_at'])
