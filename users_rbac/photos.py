"""Private user profile photos in S3 (gazebo-media-files / User-profile/)."""

import os
import uuid

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

from users_rbac.models import RbacUser

ALLOWED_CONTENT_TYPES = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
}
MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
PRESIGN_SECONDS = 3600
PREFIX = 'User-profile'


def _s3_client():
    profile = os.getenv('AWS_PROFILE') or getattr(settings, 'AWS_PROFILE', None)
    region = (
        os.getenv('AWS_DEFAULT_REGION')
        or getattr(settings, 'AWS_DEFAULT_REGION', None)
        or 'eu-west-2'
    )
    try:
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    except Exception:
        session = boto3.Session()
    return session.client('s3', region_name=region)


def _bucket() -> str:
    return getattr(settings, 'MEDIA_S3_BUCKET', None) or 'gazebo-media-files'


def photo_url(user: RbacUser, *, expires_in: int = PRESIGN_SECONDS) -> str | None:
    if not user.photo_key:
        return None
    try:
        return _s3_client().generate_presigned_url(
            'get_object',
            Params={'Bucket': _bucket(), 'Key': user.photo_key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None


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

    key = f'{PREFIX}/{user.cognito_sub}/photo-{uuid.uuid4().hex}.{ext}'
    client = _s3_client()
    bucket = _bucket()
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=uploaded_file.read(),
            ContentType=content_type,
            # Private object — no public ACL; FE uses short-lived presigned GET.
            ServerSideEncryption='AES256',
        )
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
