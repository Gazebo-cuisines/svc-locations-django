from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from purchasing.models import GoodsInFailWhen, GoodsInInputType


class QcAnswerError(ValueError):
    pass


def parse_date(value, field_name: str) -> date:
    if value in (None, ''):
        raise QcAnswerError(f'{field_name} is required.')
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise QcAnswerError(
            f'Invalid date for {field_name}. Use YYYY-MM-DD.',
        ) from exc


def normalize_bool(value):
    if isinstance(value, bool):
        return value
    if value in (1, '1', 'true', 'True', 'yes', 'YES', 'Y'):
        return True
    if value in (0, '0', 'false', 'False', 'no', 'NO', 'N'):
        return False
    raise QcAnswerError(f'Invalid yes/no value: {value!r}.')


def normalize_answer(item, raw) -> dict:
    if not isinstance(raw, dict):
        raw = {'value': raw}
    value = raw.get('value')
    comment = raw.get('comment')
    if comment in ('',):
        comment = None

    if item.input_type == GoodsInInputType.BOOL:
        if value in (None, '') and item.required:
            raise QcAnswerError(f'Answer required for {item.code}.')
        if value not in (None, ''):
            value = normalize_bool(value)
    elif item.input_type == GoodsInInputType.DECIMAL:
        if value in (None, '') and item.required:
            raise QcAnswerError(f'Answer required for {item.code}.')
        if value not in (None, ''):
            try:
                value = str(Decimal(str(value)))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise QcAnswerError(f'Invalid decimal for {item.code}.') from exc
    elif item.input_type == GoodsInInputType.DATE:
        if value in (None, '') and item.required:
            raise QcAnswerError(f'Answer required for {item.code}.')
        if value not in (None, ''):
            value = parse_date(value, item.code).isoformat()
    else:
        if value in (None, '') and item.required:
            raise QcAnswerError(f'Answer required for {item.code}.')
        if value is not None:
            value = str(value)

    if (
        item.allows_comment
        and item.fail_when == GoodsInFailWhen.TRUE
        and value is True
        and not comment
    ):
        raise QcAnswerError(
            f'Comment is required for {item.code} when answered Yes.',
        )

    return {'value': value, 'comment': comment}


def answer_fails(item, normalized: dict, *, min_value=None, max_value=None) -> bool:
    if not item.fail_when:
        return False
    value = normalized.get('value')
    if item.fail_when == GoodsInFailWhen.FALSE:
        return value is False
    if item.fail_when == GoodsInFailWhen.TRUE:
        return value is True
    if item.fail_when == GoodsInFailWhen.OUT_OF_RANGE:
        if value in (None, ''):
            return False
        try:
            num = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return True
        lo = min_value if min_value is not None else item.min_value
        hi = max_value if max_value is not None else item.max_value
        if lo is not None and num < lo:
            return True
        if hi is not None and num > hi:
            return True
        return False
    return False
