"""Location contact CRUD."""

from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.models import Location, LocationContact
from locations.utils.api_response import api_error, api_success
from locations.views.location_views import _parse_json_body, _require_admin


def _contact_dict(row: LocationContact) -> dict:
    return {
        'id': row.id,
        'location_id': row.location_id,
        'name': row.name,
        'phone': row.phone,
        'email': row.email,
        'contact_type': row.contact_type,
        'contact_value': row.contact_value,
        'remarks': row.remarks,
    }


def _apply_fields(row: LocationContact, body: dict) -> None:
    fields = ('name', 'phone', 'email', 'contact_type', 'contact_value', 'remarks')
    for key in fields:
        if key not in body:
            continue
        value = body.get(key)
        if value == '':
            value = None
        if key == 'contact_type' and value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError('contact_type must be an integer.') from exc
        setattr(row, key, value)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def location_contacts_api(request, location_id: int):
    if not Location.objects.filter(pk=location_id).exists():
        return api_error('Location not found.', status_code=404)

    if request.method == 'GET':
        rows = LocationContact.objects.filter(location_id=location_id).order_by('id')
        return api_success(
            'Contacts fetched successfully.',
            {
                'count': rows.count(),
                'results': [_contact_dict(row) for row in rows],
            },
        )

    denied = _require_admin(request)
    if denied:
        return denied

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    row = LocationContact(location_id=location_id)
    try:
        _apply_fields(row, body)
    except ValidationError as exc:
        return api_error('; '.join(exc.messages), status_code=400)
    row.save()
    return api_success('Contact created successfully.', _contact_dict(row), status_code=201)


@csrf_exempt
@require_http_methods(['GET', 'PATCH', 'DELETE'])
def location_contact_detail_api(request, location_id: int, contact_id: int):
    try:
        row = LocationContact.objects.get(pk=contact_id, location_id=location_id)
    except LocationContact.DoesNotExist:
        return api_error('Contact not found.', status_code=404)

    if request.method == 'GET':
        return api_success('Contact fetched successfully.', _contact_dict(row))

    denied = _require_admin(request)
    if denied:
        return denied

    if request.method == 'DELETE':
        row.delete()
        return api_success('Contact deleted successfully.', data=None)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        _apply_fields(row, body)
    except ValidationError as exc:
        return api_error('; '.join(exc.messages), status_code=400)
    row.save()
    return api_success('Contact updated successfully.', _contact_dict(row))
