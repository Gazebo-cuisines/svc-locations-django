from decimal import Decimal
from types import SimpleNamespace

from purchasing.services.adhoc_goods_in import get_adhoc_goods_in
from purchasing.services.goods_in_form import resolve_goods_in_form
from purchasing.services.qc_answers import answer_fails


def delivery_checklist(po_id: int, delivery_id: int) -> dict:
    return build_checklist(resolve_goods_in_form(po_id, delivery_id=delivery_id))


def adhoc_checklist(session_id: int) -> dict:
    return build_checklist(get_adhoc_goods_in(session_id))


def _dec(value):
    if value in (None, ''):
        return None
    return Decimal(str(value))


def _image_key(code: str) -> str | None:
    if code in ('delivery_date', 'comment'):
        return None
    return code.replace('_', '-')


def _answer(saved: dict, code: str) -> dict:
    raw = saved.get(code)
    if not isinstance(raw, dict):
        return {'value': raw, 'comment': None}
    return {'value': raw.get('value'), 'comment': raw.get('comment')}


def _state(item: dict, answer: dict) -> str:
    if answer.get('value') in (None, ''):
        return 'pending'
    ns = SimpleNamespace(
        fail_when=item.get('fail_when'),
        min_value=_dec(item.get('min_value')),
        max_value=_dec(item.get('max_value')),
    )
    if not answer_fails(ns, answer):
        return 'done'
    if item.get('allows_comment') and not answer.get('comment'):
        return 'needs_comment'
    return 'fail'


def build_checklist(form: dict) -> dict:
    header = form.get('header') or {}
    saved = form.get('saved_header_answers') or {}
    delivery_date = form.get('delivery_at') or form.get('suggested_delivery_date')
    steps = [{
        'id': 'delivery_date',
        'code': 'delivery_date',
        'kind': 'date',
        'label': 'Delivery date',
        'required': True,
        'group': 'delivery',
        'image_key': None,
        'sort_order': 0,
        'state': 'done' if delivery_date else 'pending',
        'value': delivery_date,
        'comment': None,
    }]
    for item in header.get('items') or []:
        code = item['code']
        answer = _answer(saved, code)
        state = _state(item, answer)
        steps.append({
            'id': code,
            'code': code,
            'kind': item['input_type'],
            'label': item['label'],
            'required': item['required'],
            'is_critical': item.get('is_critical'),
            'fail_when': item.get('fail_when'),
            'allows_comment': item.get('allows_comment'),
            'group': 'header',
            'image_key': _image_key(code),
            'sort_order': item.get('sort_order'),
            'state': state,
            'value': answer['value'],
            'comment': answer['comment'],
        })
    done = sum(1 for step in steps if step['state'] == 'done')
    return {
        'checklist_version': header.get('version'),
        'steps': steps,
        'summary': {
            'done': done,
            'total': len(steps),
            'remaining': len(steps) - done,
            'blocking': [
                step['code']
                for step in steps
                if step.get('required')
                and step['state'] in ('pending', 'needs_comment')
            ],
        },
    }
