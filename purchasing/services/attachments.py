"""Private goods-in attachments in S3 (gazebo-media-files / Goods-in/)."""

import os
import uuid

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction

from purchasing.models import (
    GoodsInAttachment,
    GoodsInAttachmentKind,
    PurchaseOrder,
    PurchaseOrderHistory,
    PurchaseOrderLine,
)

ALLOWED_CONTENT_TYPES = {
    'image/jpeg': 'jpg',
    'image/png': 'png',
    'image/webp': 'webp',
    'application/pdf': 'pdf',
}
MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
PRESIGN_SECONDS = 3600
PREFIX = 'Goods-in'


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
    attachment: GoodsInAttachment,
    *,
    expires_in: int = PRESIGN_SECONDS,
) -> str | None:
    if not attachment.s3_key:
        return None
    try:
        return _s3_client().generate_presigned_url(
            'get_object',
            Params={'Bucket': _bucket(), 'Key': attachment.s3_key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None


def attachment_dict(attachment: GoodsInAttachment) -> dict:
    return {
        'id': attachment.id,
        'purchase_order_id': attachment.purchase_order_id,
        'line_id': attachment.line_id,
        'history_id': attachment.history_id,
        'kind': attachment.kind,
        's3_key': attachment.s3_key,
        'content_type': attachment.content_type,
        'original_filename': attachment.original_filename,
        'uploaded_by_user_id': attachment.uploaded_by_user_id,
        'created_at': (
            attachment.created_at.isoformat() if attachment.created_at else None
        ),
        'url': attachment_url(attachment),
    }


def list_attachments(po_id: int) -> list[dict]:
    if not PurchaseOrder.objects.filter(pk=po_id).exists():
        raise AttachmentError('Purchase order not found.')
    rows = GoodsInAttachment.objects.filter(purchase_order_id=po_id).order_by('-id')
    return [attachment_dict(row) for row in rows]


@transaction.atomic
def upload_attachment(
    po_id: int,
    *,
    uploaded_file,
    kind: str = GoodsInAttachmentKind.PHOTO,
    line_id=None,
    history_id=None,
    uploaded_by_user_id=None,
) -> dict:
    try:
        po = PurchaseOrder.objects.get(pk=po_id)
    except PurchaseOrder.DoesNotExist as exc:
        raise AttachmentError('Purchase order not found.') from exc

    if kind not in GoodsInAttachmentKind.values:
        raise AttachmentError(
            f'Invalid kind. Use one of: {", ".join(GoodsInAttachmentKind.values)}.',
        )

    line = None
    if line_id not in (None, ''):
        try:
            line = PurchaseOrderLine.objects.get(
                pk=int(line_id), purchase_order_id=po.id,
            )
        except (PurchaseOrderLine.DoesNotExist, TypeError, ValueError) as exc:
            raise AttachmentError(f'line_id={line_id} not found on this PO.') from exc

    history = None
    if history_id not in (None, ''):
        try:
            history = PurchaseOrderHistory.objects.get(
                pk=int(history_id), purchase_order_id=po.id,
            )
        except (PurchaseOrderHistory.DoesNotExist, TypeError, ValueError) as exc:
            raise AttachmentError(
                f'history_id={history_id} not found on this PO.',
            ) from exc

    content_type = (getattr(uploaded_file, 'content_type', None) or '').lower()
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if not ext:
        raise AttachmentError('File must be a JPEG, PNG, WebP, or PDF.')

    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_BYTES:
        raise AttachmentError('File must be 5 MB or smaller.')

    key = f'{PREFIX}/{po.id}/{kind}-{uuid.uuid4().hex}.{ext}'
    client = _s3_client()
    bucket = _bucket()
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=uploaded_file.read(),
            ContentType=content_type,
            ServerSideEncryption='AES256',
        )
    except ClientError as exc:
        raise AttachmentError("We couldn't save that file. Please try again.") from exc

    row = GoodsInAttachment.objects.create(
        purchase_order=po,
        line=line,
        history=history,
        kind=kind,
        s3_key=key,
        content_type=content_type,
        original_filename=getattr(uploaded_file, 'name', None),
        uploaded_by_user_id=uploaded_by_user_id,
    )
    return attachment_dict(row)


@transaction.atomic
def delete_attachment(po_id: int, attachment_id: int) -> None:
    try:
        row = GoodsInAttachment.objects.get(
            pk=attachment_id, purchase_order_id=po_id,
        )
    except GoodsInAttachment.DoesNotExist as exc:
        raise AttachmentError('Attachment not found.') from exc

    key = row.s3_key
    row.delete()
    if key:
        try:
            _s3_client().delete_object(Bucket=_bucket(), Key=key)
        except ClientError:
            pass
