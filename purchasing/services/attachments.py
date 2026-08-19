"""Private goods-in attachments in S3 (gazebo-media-files / Goods-in/)."""

import uuid

from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction

from core.images import prepare_webp, s3_image_args
from core.s3 import s3_client as _s3_client
from purchasing.models import (
    GoodsInAttachment,
    GoodsInAttachmentKind,
    PurchaseOrder,
    PurchaseOrderDelivery,
    PurchaseOrderHistory,
    PurchaseOrderLine,
)
from users_rbac.models import RbacUser

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


def _uploader_block(user: RbacUser | None, *, user_id: int | None) -> dict:
    if user is None and user_id is None:
        return {
            'uploaded_by_user_id': None,
            'uploaded_by_username': None,
            'uploaded_by_display_name': None,
            'uploaded_by_email': None,
            'uploaded_by_photo_url': None,
            'uploaded_by_profile_path': None,
        }
    uid = user.id if user is not None else user_id
    return {
        'uploaded_by_user_id': uid,
        'uploaded_by_username': user.username if user else None,
        'uploaded_by_display_name': (
            (user.display_name or user.username) if user else None
        ),
        'uploaded_by_email': user.email if user else None,
        'uploaded_by_photo_url': None,
        # FE: navigate(uploaded_by_profile_path)
        'uploaded_by_profile_path': (
            f'/configuration/users/{uid}' if uid is not None else None
        ),
    }


def _users_by_id(user_ids: list[int | None]) -> dict[int, RbacUser]:
    ids = [i for i in user_ids if i is not None]
    if not ids:
        return {}
    return {u.id: u for u in RbacUser.objects.filter(pk__in=ids)}


def attachment_dict(
    attachment: GoodsInAttachment,
    *,
    users: dict[int, RbacUser] | None = None,
) -> dict:
    user_id = attachment.uploaded_by_user_id
    user = None
    if user_id is not None:
        if users is not None:
            user = users.get(user_id)
        else:
            user = RbacUser.objects.filter(pk=user_id).first()
    data = {
        'id': attachment.id,
        'purchase_order_id': attachment.purchase_order_id,
        'delivery_id': attachment.delivery_id,
        'line_id': attachment.line_id,
        'history_id': attachment.history_id,
        'kind': attachment.kind,
        's3_key': attachment.s3_key,
        'content_type': attachment.content_type,
        'original_filename': attachment.original_filename,
        'created_at': (
            attachment.created_at.isoformat() if attachment.created_at else None
        ),
        'url': attachment_url(attachment),
    }
    data.update(_uploader_block(user, user_id=user_id))
    return data


def list_attachments(po_id: int, delivery_id: int | None = None) -> list[dict]:
    if not PurchaseOrder.objects.filter(pk=po_id).exists():
        raise AttachmentError('Purchase order not found.')
    qs = GoodsInAttachment.objects.filter(purchase_order_id=po_id)
    if delivery_id is not None:
        qs = qs.filter(delivery_id=delivery_id)
    rows = list(qs.order_by('-id'))
    users = _users_by_id([r.uploaded_by_user_id for r in rows])
    return [attachment_dict(row, users=users) for row in rows]


@transaction.atomic
def upload_attachment(
    po_id: int,
    *,
    uploaded_file,
    kind: str = GoodsInAttachmentKind.PHOTO,
    line_id=None,
    history_id=None,
    delivery_id=None,
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

    delivery = None
    if delivery_id not in (None, ''):
        try:
            delivery = PurchaseOrderDelivery.objects.get(
                pk=int(delivery_id), purchase_order_id=po.id,
            )
        except (
            PurchaseOrderDelivery.DoesNotExist, TypeError, ValueError,
        ) as exc:
            raise AttachmentError(
                f'delivery_id={delivery_id} not found on this PO.',
            ) from exc
    else:
        delivery = PurchaseOrderDelivery.objects.filter(
            purchase_order_id=po.id,
            status='open',
        ).first()

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
        if content_type.startswith('image/'):
            try:
                body, _meta = prepare_webp(uploaded_file, max_upload=MAX_BYTES)
            except ValueError as exc:
                raise AttachmentError(str(exc)) from exc
            content_type = 'image/webp'
            key = f'{PREFIX}/{po.id}/{kind}-{uuid.uuid4().hex}.webp'
            client.put_object(Bucket=bucket, Key=key, **s3_image_args(body, _meta))
        else:
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
        delivery=delivery,
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
