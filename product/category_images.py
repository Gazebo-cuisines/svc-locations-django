"""Private category images in S3 (gazebo-media-files / Product-category/)."""

import uuid

from botocore.exceptions import ClientError
from django.conf import settings

from core.images import prepare_webp, s3_image_args
from core.s3 import s3_client as _s3_client
from product.models import Category

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
PRESIGN_SECONDS = 3600
PREFIX = 'Product-category'


def _bucket() -> str:
    return getattr(settings, 'MEDIA_S3_BUCKET', None) or 'gazebo-media-files'


def category_image_url(category: Category, *, expires_in: int = PRESIGN_SECONDS) -> str | None:
    if not category.image_key:
        return None
    try:
        return _s3_client().generate_presigned_url(
            'get_object',
            Params={'Bucket': _bucket(), 'Key': category.image_key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None


def upload_category_image(category: Category, uploaded_file) -> str:
    """
    Store file privately at Product-category/{id}/image-{uuid}.webp.
    Returns the object key. Replaces any previous key (best-effort delete).
    """
    body, meta = prepare_webp(uploaded_file, max_upload=MAX_UPLOAD_BYTES)
    key = f'{PREFIX}/{category.id}/image-{uuid.uuid4().hex}.webp'
    client = _s3_client()
    bucket = _bucket()
    try:
        client.put_object(Bucket=bucket, Key=key, **s3_image_args(body, meta))
    except ClientError as exc:
        raise ValueError("We couldn't save that image. Please try again.") from exc

    old_key = category.image_key
    category.image_key = key
    category.save(update_fields=['image_key'])
    if old_key and old_key != key:
        try:
            client.delete_object(Bucket=bucket, Key=old_key)
        except ClientError:
            pass
    return key
