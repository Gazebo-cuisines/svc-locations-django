"""Private category images in S3 (gazebo-media-files / Product-category/)."""

import os
import uuid
from io import BytesIO

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

from product.models import Category

ALLOWED_CONTENT_TYPES = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
}
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_STORE_BYTES = 2 * 1024 * 1024
MAX_EDGE = 2048
JPEG_QUALITY = 85
PRESIGN_SECONDS = 3600
PREFIX = 'Product-category'


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


def _prepare_category_image(uploaded_file) -> tuple[bytes, str, str]:
    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise ValueError('Image is too large. Use a file under 20 MB.')

    raw = uploaded_file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError('Image is too large. Use a file under 20 MB.')

    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except UnidentifiedImageError as exc:
        raise ValueError('Image must be a JPEG, PNG, or WebP.') from exc

    fmt = (image.format or '').upper()
    if fmt not in ALLOWED_FORMATS:
        raise ValueError('Image must be a JPEG, PNG, or WebP.')

    if len(raw) <= MAX_STORE_BYTES:
        ext = ALLOWED_CONTENT_TYPES.get(
            (getattr(uploaded_file, 'content_type', None) or '').lower(),
        ) or {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[fmt]
        mime = {
            'jpg': 'image/jpeg',
            'png': 'image/png',
            'webp': 'image/webp',
        }[ext]
        return raw, mime, ext

    image = ImageOps.exif_transpose(image)
    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    if image.mode in ('RGBA', 'LA', 'P'):
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    elif image.mode != 'RGB':
        image = image.convert('RGB')

    body = b''
    for quality in (JPEG_QUALITY, 82, 78):
        buf = BytesIO()
        image.save(buf, format='JPEG', quality=quality, optimize=True, progressive=True)
        body = buf.getvalue()
        if len(body) <= MAX_STORE_BYTES:
            return body, 'image/jpeg', 'jpg'

    for edge in (1600, 1280):
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        buf = BytesIO()
        image.save(buf, format='JPEG', quality=82, optimize=True, progressive=True)
        body = buf.getvalue()
        if len(body) <= MAX_STORE_BYTES:
            return body, 'image/jpeg', 'jpg'

    raise ValueError("We couldn't shrink that image enough. Try a smaller photo.")


def upload_category_image(category: Category, uploaded_file) -> str:
    """
    Store file privately at Product-category/{id}/image-{uuid}.{ext}.
    Returns the object key. Replaces any previous key (best-effort delete).
    """
    body, content_type, ext = _prepare_category_image(uploaded_file)
    key = f'{PREFIX}/{category.id}/image-{uuid.uuid4().hex}.{ext}'
    client = _s3_client()
    bucket = _bucket()
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            ServerSideEncryption='AES256',
        )
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
