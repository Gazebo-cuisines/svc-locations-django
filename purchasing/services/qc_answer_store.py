from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from purchasing.models import GoodsInAnswer, GoodsInCheckScope, GoodsInInputType


def _parse_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def infer_input_type(value) -> str:
    if isinstance(value, bool):
        return GoodsInInputType.BOOL
    parsed = _parse_date(value)
    if parsed is not None and str(value)[:10] == parsed.isoformat():
        return GoodsInInputType.DATE
    if value not in (None, ''):
        try:
            Decimal(str(value))
            return GoodsInInputType.DECIMAL
        except (InvalidOperation, TypeError, ValueError):
            pass
    return GoodsInInputType.TEXT


def _typed_values(input_type: str, value) -> dict:
    fields = {
        'value_bool': None,
        'value_decimal': None,
        'value_text': None,
        'value_date': None,
    }
    if value in (None, ''):
        return fields
    if input_type == GoodsInInputType.BOOL:
        fields['value_bool'] = bool(value)
    elif input_type == GoodsInInputType.DECIMAL:
        fields['value_decimal'] = Decimal(str(value))
    elif input_type == GoodsInInputType.DATE:
        fields['value_date'] = _parse_date(value)
    else:
        fields['value_text'] = str(value)
    return fields


def answer_json(row: GoodsInAnswer) -> dict:
    if row.input_type == GoodsInInputType.BOOL:
        value = row.value_bool
    elif row.input_type == GoodsInInputType.DECIMAL:
        value = str(row.value_decimal) if row.value_decimal is not None else None
    elif row.input_type == GoodsInInputType.DATE:
        value = row.value_date.isoformat() if row.value_date else None
    else:
        value = row.value_text
    return {'value': value, 'comment': row.comment}


def load_answers(
    *,
    delivery=None,
    delivery_line=None,
    adhoc_session=None,
    adhoc_line=None,
) -> dict:
    if delivery_line is not None:
        qs = GoodsInAnswer.objects.filter(delivery_line=delivery_line)
    elif adhoc_line is not None:
        qs = GoodsInAnswer.objects.filter(adhoc_line=adhoc_line)
    elif adhoc_session is not None:
        qs = GoodsInAnswer.objects.filter(
            adhoc_session=adhoc_session,
            scope=GoodsInCheckScope.HEADER,
            adhoc_line__isnull=True,
        )
    elif delivery is not None:
        qs = GoodsInAnswer.objects.filter(
            delivery=delivery,
            scope=GoodsInCheckScope.HEADER,
            delivery_line__isnull=True,
        )
    else:
        return {}
    rows = list(qs)
    if not rows:
        return {}
    return {row.check_code: answer_json(row) for row in rows}


def upsert_answers(
    *,
    answers: dict,
    items_by_code: dict,
    user_id: int | None,
    scope: str,
    delivery=None,
    delivery_line=None,
    adhoc_session=None,
    adhoc_line=None,
) -> None:
    if not answers:
        return
    if user_id not in (None, ''):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            user_id = None
    else:
        user_id = None
    now = timezone.now()
    for code, raw in answers.items():
        if not isinstance(raw, dict):
            raw = {'value': raw}
        item = items_by_code.get(code)
        input_type = item.input_type if item is not None else infer_input_type(
            raw.get('value'),
        )
        defaults = {
            'scope': scope,
            'input_type': input_type,
            'comment': raw.get('comment') or None,
            'answered_by_user_id': user_id,
            'answered_at': now,
            **_typed_values(input_type, raw.get('value')),
        }
        if delivery_line is not None:
            defaults['delivery'] = delivery or delivery_line.delivery
            GoodsInAnswer.objects.update_or_create(
                delivery_line=delivery_line,
                check_code=code,
                defaults=defaults,
            )
        elif adhoc_line is not None:
            defaults['adhoc_session'] = adhoc_session or adhoc_line.session
            GoodsInAnswer.objects.update_or_create(
                adhoc_line=adhoc_line,
                check_code=code,
                defaults=defaults,
            )
        elif adhoc_session is not None:
            GoodsInAnswer.objects.update_or_create(
                adhoc_session=adhoc_session,
                check_code=code,
                defaults=defaults,
            )
        elif delivery is not None:
            GoodsInAnswer.objects.update_or_create(
                delivery=delivery,
                check_code=code,
                defaults=defaults,
            )
