"""Private recipe photos in S3 (gazebo-media-files / Recipe-version/)."""

import os
import uuid

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction

from recipe.models import (
    RecipeAttachment,
    RecipeAttachmentKind,
    RecipeComponent,
    RecipeVersion,
)

ALLOWED_CONTENT_TYPES = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
}
MAX_BYTES = 5 * 1024 * 1024
PRESIGN_SECONDS = 3600
PREFIX = 'Recipe-version'


class AttachmentError(ValueError):
    pass


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


def attachment_url(
    row: RecipeAttachment, *, expires_in: int = PRESIGN_SECONDS,
) -> str | None:
    if not row.s3_key:
        return None
    try:
        return _s3_client().generate_presigned_url(
            'get_object',
            Params={'Bucket': _bucket(), 'Key': row.s3_key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None


def attachment_dict(row: RecipeAttachment) -> dict:
    return {
        'id': row.id,
        'recipe_version_id': row.recipe_version_id,
        'component_id': row.component_id,
        'kind': row.kind,
        'content_type': row.content_type,
        'original_filename': row.original_filename,
        'caption': row.caption,
        'sort_order': row.sort_order,
        'uploaded_by_sub': row.uploaded_by_sub,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'url': attachment_url(row),
    }


def list_attachments(version: RecipeVersion) -> list[dict]:
    return [attachment_dict(row) for row in version.attachments.all()]


@transaction.atomic
def upload_attachment(
    version: RecipeVersion,
    *,
    uploaded_file,
    kind: str = RecipeAttachmentKind.STEP,
    component_id=None,
    caption=None,
    sort_order=0,
    uploaded_by_sub=None,
) -> dict:
    if kind not in RecipeAttachmentKind.values:
        raise AttachmentError(
            f'Invalid kind. Use one of: {", ".join(RecipeAttachmentKind.values)}.',
        )

    component = None
    if component_id not in (None, ''):
        try:
            component = RecipeComponent.objects.get(
                pk=int(component_id), recipe_version_id=version.id,
            )
        except (RecipeComponent.DoesNotExist, TypeError, ValueError) as exc:
            raise AttachmentError(
                f'component_id={component_id} not found on this version.',
            ) from exc

    content_type = (getattr(uploaded_file, 'content_type', None) or '').lower()
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if not ext:
        raise AttachmentError('File must be a JPEG, PNG, or WebP.')

    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_BYTES:
        raise AttachmentError('File must be 5 MB or smaller.')

    try:
        sort_order = int(sort_order or 0)
    except (TypeError, ValueError) as exc:
        raise AttachmentError('sort_order must be an integer.') from exc

    key = f'{PREFIX}/{version.id}/{uuid.uuid4().hex}.{ext}'
    client = _s3_client()
    try:
        client.put_object(
            Bucket=_bucket(),
            Key=key,
            Body=uploaded_file.read(),
            ContentType=content_type,
            ServerSideEncryption='AES256',
        )
    except ClientError as exc:
        raise AttachmentError("We couldn't save that file. Please try again.") from exc

    row = RecipeAttachment.objects.create(
        recipe_version=version,
        component=component,
        kind=kind,
        s3_key=key,
        content_type=content_type,
        original_filename=getattr(uploaded_file, 'name', None),
        caption=caption or None,
        sort_order=sort_order,
        uploaded_by_sub=uploaded_by_sub,
    )
    return attachment_dict(row)


@transaction.atomic
def delete_attachment(row: RecipeAttachment) -> None:
    key = row.s3_key
    row.delete()
    if key:
        try:
            _s3_client().delete_object(Bucket=_bucket(), Key=key)
        except ClientError:
            pass
