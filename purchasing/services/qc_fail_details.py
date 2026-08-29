"""Human-readable line-QC failure details for mobile (FCA wording)."""

from __future__ import annotations

from datetime import date


def _dedupe(codes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def build_line_qc_fail_details(
    *,
    failed_codes: list[str],
    items_by_code: dict,
    normalized: dict,
    delivery_date: date | None = None,
) -> list[dict]:
    """
    Server-owned messages for each failed check code.
    Mobile should display detail['message'] as-is.
    """
    details: list[dict] = []
    for code in _dedupe(failed_codes):
        item = items_by_code.get(code)
        label = item.label if item is not None else code.replace('_', ' ').title()
        answer = normalized.get(code) or {}
        value = answer.get('value')

        if code == 'use_by' and answer.get('shelf_life_fail'):
            min_days = answer.get('min_acceptable_shelf_life_days')
            detail = {
                'code': code,
                'label': label,
                'reason': 'shelf_life',
                'message': (
                    f'Use-by must be at least {min_days} days after delivery.'
                    if min_days is not None
                    else 'Use-by fails minimum shelf life from delivery.'
                ),
                'min_days': min_days,
                'delivery_date': (
                    delivery_date.isoformat() if delivery_date else None
                ),
                'use_by': value,
            }
            details.append(detail)
            continue

        if code == 'product_temperature':
            bounds = answer.get('bounds') or {}
            lo = bounds.get('min')
            hi = bounds.get('max')
            if lo is not None and hi is not None:
                message = (
                    f'Temperature must be between {lo} and {hi} °C.'
                )
            elif lo is not None:
                message = f'Temperature must be at least {lo} °C.'
            elif hi is not None:
                message = f'Temperature must be at most {hi} °C.'
            else:
                message = f'{label} is outside the allowed range.'
            details.append({
                'code': code,
                'label': label,
                'reason': 'out_of_range',
                'message': message,
                'bounds': bounds or None,
                'value': value if value is not None else None,
            })
            continue

        details.append({
            'code': code,
            'label': label,
            'reason': 'failed',
            'message': f'{label} failed QC.',
            'value': value if value is not None else None,
        })
    return details


def line_qc_blocked_message() -> str:
    return 'Inform QC/QA — receive blocked until line passes.'
