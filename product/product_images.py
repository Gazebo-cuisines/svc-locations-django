"""Private product photos in S3 (gazebo-media-files / Product/)."""

import uuid

from botocore.exceptions import ClientError
from django.conf import settings
from django.db import DatabaseError, transaction

from core.images import prepare_webp, s3_image_args
from core.s3 import s3_client as _s3_client
from product.category_images import PRESIGN_SECONDS
from product.models import ProductImage

PREFIX = 'Product'


def _bucket() -> str:
    return getattr(settings, 'MEDIA_S3_BUCKET', None) or 'gazebo-media-files'


def product_image_url(row: ProductImage, *, expires_in: int = PRESIGN_SECONDS) -> str | None:
    if not row.image_key:
        return None
    try:
        return _s3_client().generate_presigned_url(
            'get_object',
            Params={'Bucket': _bucket(), 'Key': row.image_key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None


def product_image_dict(row: ProductImage) -> dict:
    return {
        'id': row.id,
        'product_id': row.product_id,
        'is_main': row.is_main,
        'sort_order': row.sort_order,
        'original_filename': row.original_filename,
        'url': product_image_url(row),
        'created_at': row.created_at.isoformat() if row.created_at else None,
    }


def list_product_images(product_id: int) -> list[dict]:
    return [
        product_image_dict(row)
        for row in ProductImage.objects.filter(product_id=product_id)
    ]


def product_photos(product) -> tuple[str | None, list[dict]]:
    try:
        payload = [product_image_dict(row) for row in product.images.all()]
    except DatabaseError:
        return None, []
    main = next((item['url'] for item in payload if item['is_main']), None)
    return main, payload


def _set_main(product_id: int, image_id: int) -> None:
    ProductImage.objects.filter(product_id=product_id, is_main=True).exclude(
        pk=image_id,
    ).update(is_main=False)
    ProductImage.objects.filter(pk=image_id, product_id=product_id).update(is_main=True)


@transaction.atomic
def upload_product_image(
    product,
    uploaded_file,
    *,
    is_main: bool = False,
    sort_order: int = 0,
) -> dict:
    body, meta = prepare_webp(uploaded_file)
    key = f'{PREFIX}/{product.id}/image-{uuid.uuid4().hex}.webp'
    client = _s3_client()
    try:
        client.put_object(Bucket=_bucket(), Key=key, **s3_image_args(body, meta))
    except ClientError as exc:
        raise ValueError("We couldn't save that image. Please try again.") from exc

    make_main = is_main or not ProductImage.objects.filter(product_id=product.id).exists()
    row = ProductImage.objects.create(
        product=product,
        image_key=key,
        is_main=False,
        sort_order=sort_order,
        original_filename=getattr(uploaded_file, 'name', None),
    )
    if make_main:
        _set_main(product.id, row.id)
        row.is_main = True
    return product_image_dict(row)


@transaction.atomic
def update_product_image(row: ProductImage, *, is_main=None, sort_order=None) -> dict:
    if sort_order is not None:
        row.sort_order = sort_order
        row.save(update_fields=['sort_order'])
    if is_main is True:
        _set_main(row.product_id, row.id)
    elif is_main is False and row.is_main:
        raise ValueError('Set another photo as main instead of clearing this one.')
    return product_image_dict(ProductImage.objects.get(pk=row.pk))


@transaction.atomic
def delete_product_image(row: ProductImage) -> None:
    product_id = row.product_id
    was_main = row.is_main
    key = row.image_key
    row.delete()
    if was_main:
        nxt = (
            ProductImage.objects.filter(product_id=product_id)
            .order_by('sort_order', 'id')
            .first()
        )
        if nxt is not None:
            nxt.is_main = True
            nxt.save(update_fields=['is_main'])
    if key:
        try:
            _s3_client().delete_object(Bucket=_bucket(), Key=key)
        except ClientError:
            pass
