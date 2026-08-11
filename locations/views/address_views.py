"""Location postal address CRUD (single joined address text)."""

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.models import Location, LocationAddress
from locations.utils.api_response import api_error, api_success
from locations.views.location_views import _parse_json_body, _require_admin

LINE_KEYS = (
    'address_line_1',
    'address_line_2',
    'address_line_3',
    'address_line_4',
    'address_line_5',
    # Sage-style aliases
    'compact_address_line_1',
    'compact_address_line_2',
    'compact_address_line_3',
    'compact_address_line_4',
    'compact_address_line_5',
)


def _address_dict(row: LocationAddress) -> dict:
    return {
        'id': row.id,
        'location_id': row.location_id,
        'name': row.name,
        'contact_point_name': row.contact_point_name,
        'contact_point_phone': row.contact_point_phone,
        'address': row.address,
        'is_primary': row.is_primary,
    }


def _join_address(body: dict) -> str | None:
    if 'address' in body:
        value = body.get('address')
        return None if value in (None, '') else str(value)

    lines = []
    for i in range(1, 6):
        for key in (f'address_line_{i}', f'compact_address_line_{i}', f'line{i}'):
            if key in body and body.get(key) not in (None, ''):
                lines.append(str(body.get(key)).strip())
                break
    if not lines:
        return None
    return '\n'.join(lines)


def _apply_fields(row: LocationAddress, body: dict) -> None:
    for key in ('name', 'contact_point_name', 'contact_point_phone'):
        if key not in body:
            continue
        value = body.get(key)
        setattr(row, key, None if value == '' else value)

    if 'is_primary' in body:
        row.is_primary = bool(body.get('is_primary'))

    joined = None
    if 'address' in body or any(k in body for k in LINE_KEYS) or any(
        f'line{i}' in body for i in range(1, 6)
    ):
        joined = _join_address(body)
        row.address = joined


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def location_postal_addresses_api(request, location_id: int):
    if not Location.objects.filter(pk=location_id).exists():
        return api_error('Location not found.', status_code=404)

    if request.method == 'GET':
        rows = LocationAddress.objects.filter(location_id=location_id).order_by(
            '-is_primary', 'id',
        )
        return api_success(
            'Postal addresses fetched successfully.',
            {
                'count': rows.count(),
                'results': [_address_dict(row) for row in rows],
            },
        )

    denied = _require_admin(request)
    if denied:
        return denied

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    row = LocationAddress(location_id=location_id)
    _apply_fields(row, body)
    if row.is_primary:
        LocationAddress.objects.filter(
            location_id=location_id, is_primary=True,
        ).update(is_primary=False)
    row.save()
    return api_success(
        'Postal address created successfully.',
        _address_dict(row),
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def location_postal_address_detail_api(request, location_id: int, address_id: int):
    try:
        row = LocationAddress.objects.get(pk=address_id, location_id=location_id)
    except LocationAddress.DoesNotExist:
        return api_error('Postal address not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Postal address fetched successfully.', _address_dict(row))

    denied = _require_admin(request)
    if denied:
        return denied

    if request.method == 'DELETE':
        row.delete()
        return api_success('Postal address deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    _apply_fields(row, body)
    if body.get('is_primary') is True:
        LocationAddress.objects.filter(
            location_id=location_id, is_primary=True,
        ).exclude(pk=row.id).update(is_primary=False)
    row.save()
    return api_success('Postal address updated successfully.', _address_dict(row))
