"""Private user profile photos in S3 (gazebo-media-files / User-profile/)."""

import uuid

from botocore.exceptions import ClientError

from core.images import prepare_webp, s3_image_args
from core.s3 import media_bucket, presigned_get, s3_client as _s3_client
from users_rbac.models import RbacUser

ALLOWED_CONTENT_TYPES = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
}
MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
PRESIGN_SECONDS = 3600
PREFIX = 'User-profile'


def _bucket() -> str:
    return media_bucket()


def photo_url(user: RbacUser, *, expires_in: int = PRESIGN_SECONDS) -> str | None:
    if not user.photo_key:
        return None
    return presigned_get(user.photo_key, expires_in=expires_in)


def upload_photo(user: RbacUser, uploaded_file) -> str:
    """
    Store file privately at User-profile/{cognito_sub}/photo-{uuid}.{ext}.
    Returns the object key. Replaces any previous key (best-effort delete).
    """
    content_type = (getattr(uploaded_file, 'content_type', None) or '').lower()
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if not ext:
        raise ValueError('Photo must be a JPEG, PNG, or WebP image.')

    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_BYTES:
        raise ValueError('Photo must be 2 MB or smaller.')

    body, meta = prepare_webp(uploaded_file, max_upload=MAX_BYTES)
    key = f'{PREFIX}/{user.cognito_sub}/photo-{uuid.uuid4().hex}.webp'
    client = _s3_client()
    bucket = _bucket()
    try:
        client.put_object(Bucket=bucket, Key=key, **s3_image_args(body, meta))
    except ClientError as exc:
        raise ValueError("We couldn't save that photo. Please try again.") from exc

    old_key = user.photo_key
    user.photo_key = key
    user.save(update_fields=['photo_key', 'updated_at'])
    if old_key and old_key != key:
        try:
            client.delete_object(Bucket=bucket, Key=old_key)
        except ClientError:
            pass
    return key
