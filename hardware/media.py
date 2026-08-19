"""Private device feed media in S3 (gazebo-media-files / Hardware/)."""

import uuid

from botocore.exceptions import ClientError
from django.conf import settings

from core.s3 import s3_client as _s3_client
from hardware.models import HardwareDevice, HardwareDevicePost
from users_rbac.photos import photo_url

ALLOWED = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
    'video/mp4': 'mp4',
    'video/quicktime': 'mov',
    'video/webm': 'webm',
}
MAX_BYTES = 20 * 1024 * 1024
PRESIGN_SECONDS = 3600
PREFIX = 'Hardware'


def _bucket() -> str:
    return getattr(settings, 'MEDIA_S3_BUCKET', None) or 'gazebo-media-files'


def media_url(row: HardwareDevicePost | None, *, expires_in: int = PRESIGN_SECONDS) -> str | None:
    if row is None or not row.media_key:
        return None
    try:
        return _s3_client().generate_presigned_url(
            'get_object',
            Params={'Bucket': _bucket(), 'Key': row.media_key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None


def post_dict(row: HardwareDevicePost) -> dict:
    user = row.user
    device = row.device
    kind = 'video' if (row.content_type or '').startswith('video/') else 'image'
    return {
        'id': row.id,
        'device_id': row.device_id,
        'device_code': device.code if device else None,
        'device_nickname': device.nickname if device else None,
        'caption': row.caption,
        'media_url': media_url(row),
        'content_type': row.content_type,
        'kind': kind,
        'user_id': row.user_id,
        'username': user.username if user else None,
        'display_name': user.display_name if user else None,
        'user_photo_url': photo_url(user) if user else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def upload_post(device: HardwareDevice, uploaded_file, *, user, caption: str = '') -> HardwareDevicePost:
    content_type = (getattr(uploaded_file, 'content_type', None) or '').lower()
    ext = ALLOWED.get(content_type)
    if not ext:
        raise ValueError('File must be a JPEG, PNG, WebP, GIF, MP4, MOV, or WebM.')
    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_BYTES:
        raise ValueError('File must be 20 MB or smaller.')
    key = f'{PREFIX}/{device.code}/post-{uuid.uuid4().hex}.{ext}'
    try:
        _s3_client().put_object(
            Bucket=_bucket(),
            Key=key,
            Body=uploaded_file.read(),
            ContentType=content_type,
            ServerSideEncryption='AES256',
        )
    except ClientError as exc:
        raise ValueError("We couldn't save that file. Please try again.") from exc
    return HardwareDevicePost.objects.create(
        device=device,
        user=user,
        caption=(caption or '')[:512],
        media_key=key,
        content_type=content_type,
    )


def delete_post(row: HardwareDevicePost) -> None:
    key = row.media_key
    row.delete()
    if not key:
        return
    try:
        _s3_client().delete_object(Bucket=_bucket(), Key=key)
    except ClientError:
        pass
