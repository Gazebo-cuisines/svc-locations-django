"""Private device feed media in S3 (gazebo-media-files / Hardware/)."""

import uuid

from botocore.exceptions import ClientError

from core.images import prepare_webp, prepare_webp_bytes, s3_image_args
from core.s3 import media_bucket, presigned_get, s3_client as _s3_client
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
    return media_bucket()


def media_url(row: HardwareDevicePost | None, *, expires_in: int = PRESIGN_SECONDS) -> str | None:
    if row is None or not row.media_key:
        return None
    return presigned_get(row.media_key, expires_in=expires_in)


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
        'metadata': row.metadata_json,
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
    meta = None
    if content_type.startswith('image/'):
        body, meta = prepare_webp(uploaded_file, max_upload=MAX_BYTES)
        content_type = 'image/webp'
        ext = 'webp'
        put = s3_image_args(body, meta)
    else:
        put = {
            'Body': uploaded_file.read(),
            'ContentType': content_type,
            'ServerSideEncryption': 'AES256',
        }
    key = f'{PREFIX}/{device.code}/post-{uuid.uuid4().hex}.{ext}'
    try:
        _s3_client().put_object(Bucket=_bucket(), Key=key, **put)
    except ClientError as exc:
        raise ValueError("We couldn't save that file. Please try again.") from exc
    return HardwareDevicePost.objects.create(
        device=device,
        user=user,
        caption=(caption or '')[:512],
        media_key=key,
        content_type=content_type,
        metadata_json=meta,
    )


def recompress_post(row: HardwareDevicePost) -> bool:
    """Rewrite a JPEG/PNG feed object as WebP. No-op for video or already-webp."""
    if not row.media_key or (row.content_type or '').startswith('video/'):
        return False
    if row.media_key.endswith('.webp') and (row.content_type or '') == 'image/webp':
        return False
    client = _s3_client()
    bucket = _bucket()
    try:
        raw = client.get_object(Bucket=bucket, Key=row.media_key)['Body'].read()
    except ClientError:
        return False
    body, meta = prepare_webp_bytes(raw, content_type=row.content_type or '')
    new_key = f'{PREFIX}/{row.device.code}/post-{uuid.uuid4().hex}.webp'
    client.put_object(Bucket=bucket, Key=new_key, **s3_image_args(body, meta))
    old_key = row.media_key
    row.media_key = new_key
    row.content_type = 'image/webp'
    row.metadata_json = meta
    row.save(update_fields=['media_key', 'content_type', 'metadata_json'])
    if old_key != new_key:
        try:
            client.delete_object(Bucket=bucket, Key=old_key)
        except ClientError:
            pass
    return True


def delete_post(row: HardwareDevicePost) -> None:
    key = row.media_key
    row.delete()
    if not key:
        return
    try:
        _s3_client().delete_object(Bucket=_bucket(), Key=key)
    except ClientError:
        pass
