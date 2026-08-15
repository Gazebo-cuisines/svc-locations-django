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

MIME_TO_EXT = {
    'image/jpeg': 'jpg',
    'image/jpg': 'jpg',
    'image/pjpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'image/gif': 'gif',
    'image/bmp': 'bmp',
    'image/heic': 'heic',
    'image/heif': 'heif',
    'image/tiff': 'tiff',
    'image/tif': 'tiff',
    'video/mp4': 'mp4',
    'video/quicktime': 'mov',
    'video/webm': 'webm',
    'video/x-msvideo': 'avi',
    'video/x-matroska': 'mkv',
    'video/mpeg': 'mpeg',
    'video/3gpp': '3gp',
    'audio/mpeg': 'mp3',
    'audio/mp3': 'mp3',
    'audio/mp4': 'm4a',
    'audio/wav': 'wav',
    'audio/x-wav': 'wav',
    'audio/aac': 'aac',
    'audio/ogg': 'ogg',
    'application/pdf': 'pdf',
    'application/msword': 'doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.ms-excel': 'xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.ms-powerpoint': 'ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
    'application/vnd.oasis.opendocument.text': 'odt',
    'application/vnd.oasis.opendocument.spreadsheet': 'ods',
    'text/csv': 'csv',
    'text/plain': 'txt',
    'application/rtf': 'rtf',
    'text/rtf': 'rtf',
}
EXT_TO_MIME = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'webp': 'image/webp',
    'gif': 'image/gif',
    'bmp': 'image/bmp',
    'heic': 'image/heic',
    'heif': 'image/heif',
    'tif': 'image/tiff',
    'tiff': 'image/tiff',
    'mp4': 'video/mp4',
    'mov': 'video/quicktime',
    'webm': 'video/webm',
    'avi': 'video/x-msvideo',
    'mkv': 'video/x-matroska',
    'mpeg': 'video/mpeg',
    'mpg': 'video/mpeg',
    '3gp': 'video/3gpp',
    'mp3': 'audio/mpeg',
    'm4a': 'audio/mp4',
    'wav': 'audio/wav',
    'aac': 'audio/aac',
    'ogg': 'audio/ogg',
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'ppt': 'application/vnd.ms-powerpoint',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'odt': 'application/vnd.oasis.opendocument.text',
    'ods': 'application/vnd.oasis.opendocument.spreadsheet',
    'csv': 'text/csv',
    'txt': 'text/plain',
    'rtf': 'application/rtf',
}
MAX_BYTES = 50 * 1024 * 1024
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
    content_type = content_type.split(';', 1)[0].strip()
    ext = MIME_TO_EXT.get(content_type)
    if not ext:
        suffix = (getattr(uploaded_file, 'name', None) or '').rsplit('.', 1)
        tail = suffix[-1].lower() if len(suffix) == 2 else ''
        content_type = EXT_TO_MIME.get(tail, '')
        ext = MIME_TO_EXT.get(content_type)
    if not ext:
        raise AttachmentError(
            'That file type is not supported. Use an image, video, audio, PDF, or office document.',
        )

    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_BYTES:
        raise AttachmentError('File must be 50 MB or smaller.')

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
