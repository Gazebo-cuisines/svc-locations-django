from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q

from product.models import ProductGoodsInType, ProductStorageRegime
from purchasing.models import (
    AdhocGoodsInLine,
    AdhocGoodsInSession,
    GoodsInCheckItem,
    GoodsInCheckScope,
    GoodsInCheckTemplate,
    GoodsInFailWhen,
    GoodsInInputType,
    PurchaseOrder,
    PurchaseOrderDelivery,
    PurchaseOrderDeliveryLine,
    PurchaseOrderLine,
)
from purchasing.services.goods_in_form import _document_dict


class CheckTemplateError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _item_dict(item: GoodsInCheckItem) -> dict:
    return {
        'id': item.id,
        'code': item.code,
        'label': item.label,
        'input_type': item.input_type,
        'required': item.required,
        'is_critical': item.is_critical,
        'fail_when': item.fail_when,
        'min_value': str(item.min_value) if item.min_value is not None else None,
        'max_value': str(item.max_value) if item.max_value is not None else None,
        'source': item.source,
        'allows_comment': item.allows_comment,
        'sort_order': item.sort_order,
    }


def template_in_use(template_id: int) -> bool:
    header = Q(header_template_id=template_id, checked_at__isnull=False)
    if PurchaseOrderDelivery.objects.filter(header).exists():
        return True
    if PurchaseOrder.objects.filter(header).exists():
        return True
    if AdhocGoodsInSession.objects.filter(header).exists():
        return True
    line = Q(line_template_id=template_id) & (
        Q(line_check_ok=True) | ~Q(line_checks={})
    )
    if PurchaseOrderDeliveryLine.objects.filter(line).exists():
        return True
    if PurchaseOrderLine.objects.filter(line).exists():
        return True
    if AdhocGoodsInLine.objects.filter(line).exists():
        return True
    return False


def _guard_items_mutable(template: GoodsInCheckTemplate) -> None:
    if template_in_use(template.id):
        raise CheckTemplateError(
            'This version already has submitted QC. '
            'Create a new version instead of editing questions.',
            status_code=409,
        )


def template_dict(template: GoodsInCheckTemplate) -> dict:
    items = [_item_dict(item) for item in template.items.all()]
    return {
        'id': template.id,
        'name': template.name,
        'goods_in_type': template.goods_in_type,
        'storage_regime': template.storage_regime,
        'scope': template.scope,
        'version': template.version,
        'is_active': template.is_active,
        'in_use': template_in_use(template.id),
        'item_count': len(items),
        'document': _document_dict(template),
        'items': items,
        'updated_at': template.updated_at.isoformat() if template.updated_at else None,
    }


def _get_template(template_id: int) -> GoodsInCheckTemplate:
    try:
        return GoodsInCheckTemplate.objects.prefetch_related('items').get(
            pk=template_id,
        )
    except GoodsInCheckTemplate.DoesNotExist as exc:
        raise CheckTemplateError('Check template not found.', status_code=404) from exc


def _parse_date(value, field: str):
    if value in (None, ''):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise CheckTemplateError(f'Invalid date for {field}. Use YYYY-MM-DD.') from exc


def _parse_decimal(value, field: str):
    if value in (None, ''):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CheckTemplateError(f'Invalid decimal for {field}.') from exc


def _parse_bool(value, default=None):
    if value in (None, ''):
        return default
    if isinstance(value, bool):
        return value
    if value in (1, '1', 'true', 'True', 'yes'):
        return True
    if value in (0, '0', 'false', 'False', 'no'):
        return False
    raise CheckTemplateError(f'Invalid yes/no value: {value!r}.')


def _parse_choice(value, allowed, field: str, *, required=True):
    if value in (None, ''):
        if required:
            raise CheckTemplateError(f'{field} is required.')
        return None
    value = str(value)
    if value not in allowed:
        raise CheckTemplateError(
            f'Invalid {field}. Use one of: {", ".join(allowed)}.',
        )
    return value


def _parse_code(value) -> str:
    code = (value or '').strip()
    if not code:
        raise CheckTemplateError('code is required.')
    if not code.replace('_', '').isalnum() or not code[0].isalpha():
        raise CheckTemplateError(
            'code must start with a letter and use letters, numbers, or underscore.',
        )
    return code


def _item_kwargs(raw: dict, *, partial=False) -> dict:
    if not isinstance(raw, dict):
        raise CheckTemplateError('Each item must be an object.')
    data = {}
    if not partial or 'code' in raw:
        data['code'] = _parse_code(raw.get('code'))
    if not partial or 'label' in raw:
        label = (raw.get('label') or '').strip()
        if not label:
            raise CheckTemplateError('label is required.')
        data['label'] = label
    if not partial or 'input_type' in raw:
        data['input_type'] = _parse_choice(
            raw.get('input_type'), GoodsInInputType.values, 'input_type',
        )
    if not partial or 'required' in raw:
        data['required'] = _parse_bool(raw.get('required'), default=True)
    if not partial or 'is_critical' in raw:
        data['is_critical'] = _parse_bool(raw.get('is_critical'), default=False)
    if not partial or 'fail_when' in raw:
        data['fail_when'] = _parse_choice(
            raw.get('fail_when'), GoodsInFailWhen.values, 'fail_when',
            required=False,
        )
    if not partial or 'min_value' in raw:
        data['min_value'] = _parse_decimal(raw.get('min_value'), 'min_value')
    if not partial or 'max_value' in raw:
        data['max_value'] = _parse_decimal(raw.get('max_value'), 'max_value')
    if not partial or 'source' in raw:
        source = raw.get('source')
        data['source'] = (str(source).strip() or None) if source not in (None, '') else None
    if not partial or 'allows_comment' in raw:
        data['allows_comment'] = _parse_bool(raw.get('allows_comment'), default=True)
    if not partial or 'sort_order' in raw:
        raw_sort = raw.get('sort_order', 0 if not partial else None)
        if raw_sort in (None, ''):
            if not partial:
                data['sort_order'] = 0
        else:
            try:
                data['sort_order'] = int(raw_sort)
            except (TypeError, ValueError) as exc:
                raise CheckTemplateError('sort_order must be an integer.') from exc
    return data


def _doc_kwargs(body: dict) -> dict:
    data = {}
    if 'document_no' in body:
        data['document_no'] = (body.get('document_no') or 'GFF001F')[:32]
    if 'issue_no' in body:
        try:
            data['issue_no'] = int(body['issue_no'])
        except (TypeError, ValueError) as exc:
            raise CheckTemplateError('issue_no must be an integer.') from exc
    for field in ('issue_date', 'review_date', 'previous_issue_date'):
        if field in body:
            data[field] = _parse_date(body.get(field), field)
    if 'reason_for_change' in body:
        reason = body.get('reason_for_change')
        data['reason_for_change'] = (str(reason).strip() or None) if reason else None
    return data


def _next_version(goods_in_type, storage_regime, scope) -> int:
    qs = GoodsInCheckTemplate.objects.filter(
        goods_in_type=goods_in_type,
        scope=scope,
    )
    qs = (
        qs.filter(storage_regime__isnull=True)
        if storage_regime is None
        else qs.filter(storage_regime=storage_regime)
    )
    current = qs.order_by('-version').values_list('version', flat=True).first()
    return (current or 0) + 1


def list_templates(*, filters: dict) -> list[dict]:
    qs = GoodsInCheckTemplate.objects.prefetch_related('items').all()
    gin = filters.get('goods_in_type')
    if gin:
        qs = qs.filter(goods_in_type=_parse_choice(
            gin, ProductGoodsInType.values, 'goods_in_type',
        ))
    if 'storage_regime' in filters:
        raw = filters.get('storage_regime')
        if raw in (None, '', '*'):
            qs = qs.filter(storage_regime__isnull=True)
        else:
            qs = qs.filter(storage_regime=_parse_choice(
                raw, ProductStorageRegime.values, 'storage_regime',
            ))
    scope = filters.get('scope')
    if scope:
        qs = qs.filter(scope=_parse_choice(scope, GoodsInCheckScope.values, 'scope'))
    if 'is_active' in filters and filters['is_active'] not in (None, ''):
        qs = qs.filter(is_active=_parse_bool(filters['is_active']))
    qs = qs.order_by('goods_in_type', 'storage_regime', 'scope', '-version')
    rows = [template_dict(row) for row in qs]
    if _parse_bool(filters.get('latest'), default=False):
        seen = set()
        latest = []
        for row in rows:
            key = (row['goods_in_type'], row['storage_regime'], row['scope'])
            if key in seen:
                continue
            seen.add(key)
            latest.append(row)
        return latest
    return rows


def get_template(template_id: int) -> dict:
    return template_dict(_get_template(template_id))


def _create_items(template: GoodsInCheckTemplate, items: list) -> None:
    if not isinstance(items, list) or not items:
        raise CheckTemplateError('items must be a non-empty list.')
    codes = []
    rows = []
    for raw in items:
        kwargs = _item_kwargs(raw)
        if kwargs['code'] in codes:
            raise CheckTemplateError(f'Duplicate item code: {kwargs["code"]}.')
        codes.append(kwargs['code'])
        rows.append(GoodsInCheckItem(template=template, **kwargs))
    GoodsInCheckItem.objects.bulk_create(rows)


@transaction.atomic
def create_template(body: dict) -> dict:
    clone_id = body.get('clone_from_id')
    source = _get_template(int(clone_id)) if clone_id not in (None, '') else None

    goods_in_type = _parse_choice(
        body.get('goods_in_type') or (source.goods_in_type if source else None),
        ProductGoodsInType.values,
        'goods_in_type',
    )
    if 'storage_regime' in body:
        storage_regime = _parse_choice(
            body.get('storage_regime'),
            ProductStorageRegime.values,
            'storage_regime',
            required=False,
        )
    else:
        storage_regime = source.storage_regime if source else None
    scope = _parse_choice(
        body.get('scope') or (source.scope if source else None),
        GoodsInCheckScope.values,
        'scope',
    )
    name = (body.get('name') or (source.name if source else '') or '').strip()
    if not name:
        raise CheckTemplateError('name is required.')

    version = body.get('version')
    if version in (None, ''):
        version = _next_version(goods_in_type, storage_regime, scope)
    else:
        try:
            version = int(version)
        except (TypeError, ValueError) as exc:
            raise CheckTemplateError('version must be an integer.') from exc

    doc = {}
    if source is not None:
        doc = {
            'document_no': source.document_no,
            'issue_no': source.issue_no,
            'issue_date': source.issue_date,
            'review_date': source.review_date,
            'previous_issue_date': source.previous_issue_date,
            'reason_for_change': source.reason_for_change,
        }
    doc.update(_doc_kwargs(body))

    template = GoodsInCheckTemplate(
        name=name,
        goods_in_type=goods_in_type,
        storage_regime=storage_regime,
        scope=scope,
        version=version,
        is_active=_parse_bool(body.get('is_active'), default=True),
        **doc,
    )
    try:
        template.save()
    except IntegrityError as exc:
        raise CheckTemplateError(
            'A template with this type, regime, scope, and version already exists.',
            status_code=409,
        ) from exc

    items = body.get('items')
    if items:
        _create_items(template, items)
    elif source is not None:
        GoodsInCheckItem.objects.bulk_create([
            GoodsInCheckItem(
                template=template,
                code=item.code,
                label=item.label,
                input_type=item.input_type,
                required=item.required,
                is_critical=item.is_critical,
                fail_when=item.fail_when,
                min_value=item.min_value,
                max_value=item.max_value,
                source=item.source,
                allows_comment=item.allows_comment,
                sort_order=item.sort_order,
            )
            for item in source.items.all()
        ])
    else:
        raise CheckTemplateError('items is required when clone_from_id is not set.')

    if source is not None and _parse_bool(
        body.get('deactivate_previous'), default=True,
    ):
        GoodsInCheckTemplate.objects.filter(
            goods_in_type=goods_in_type,
            storage_regime=storage_regime,
            scope=scope,
        ).exclude(pk=template.id).update(is_active=False)

    return template_dict(_get_template(template.id))


@transaction.atomic
def update_template(template_id: int, body: dict) -> dict:
    template = _get_template(template_id)
    if any(key in body for key in ('goods_in_type', 'storage_regime', 'scope', 'version')):
        raise CheckTemplateError(
            'type, regime, scope, and version cannot change. Create a new version.',
        )
    if 'name' in body:
        name = (body.get('name') or '').strip()
        if not name:
            raise CheckTemplateError('name is required.')
        template.name = name
    if 'is_active' in body:
        template.is_active = _parse_bool(body.get('is_active'), default=template.is_active)
    for field, value in _doc_kwargs(body).items():
        setattr(template, field, value)
    template.save()
    return template_dict(_get_template(template.id))


@transaction.atomic
def add_item(template_id: int, body: dict) -> dict:
    template = _get_template(template_id)
    _guard_items_mutable(template)
    kwargs = _item_kwargs(body)
    if template.items.filter(code=kwargs['code']).exists():
        raise CheckTemplateError(
            f'Item {kwargs["code"]} already exists on this template.',
            status_code=409,
        )
    item = GoodsInCheckItem.objects.create(template=template, **kwargs)
    return _item_dict(item)


@transaction.atomic
def update_item(template_id: int, item_id: int, body: dict) -> dict:
    template = _get_template(template_id)
    _guard_items_mutable(template)
    try:
        item = template.items.get(pk=item_id)
    except GoodsInCheckItem.DoesNotExist as exc:
        raise CheckTemplateError('Check item not found.', status_code=404) from exc
    kwargs = _item_kwargs(body, partial=True)
    if 'code' in kwargs and template.items.exclude(pk=item.id).filter(
        code=kwargs['code'],
    ).exists():
        raise CheckTemplateError(
            f'Item {kwargs["code"]} already exists on this template.',
            status_code=409,
        )
    for field, value in kwargs.items():
        setattr(item, field, value)
    item.save()
    return _item_dict(item)


@transaction.atomic
def delete_item(template_id: int, item_id: int) -> None:
    template = _get_template(template_id)
    _guard_items_mutable(template)
    deleted, _ = template.items.filter(pk=item_id).delete()
    if not deleted:
        raise CheckTemplateError('Check item not found.', status_code=404)
