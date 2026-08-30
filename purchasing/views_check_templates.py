import json
from json import JSONDecodeError

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from purchasing.services.check_templates import (
    CheckTemplateError,
    add_item,
    create_template,
    delete_item,
    get_template,
    list_templates,
    update_item,
    update_template,
)
from users_rbac.auth import require_admin


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (JSONDecodeError, UnicodeDecodeError):
        return None


def _fail(exc: CheckTemplateError):
    return api_error(str(exc), status_code=exc.status_code)


@csrf_exempt
@require_admin
@require_http_methods(['GET', 'POST'])
def check_template_collection_api(request):
    if request.method == 'GET':
        try:
            rows = list_templates(filters=request.GET.dict())
        except CheckTemplateError as exc:
            return _fail(exc)
        return api_success(
            'Check templates fetched successfully.',
            {'count': len(rows), 'results': rows},
        )
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        data = create_template(body)
    except CheckTemplateError as exc:
        return _fail(exc)
    return api_success('Check template created successfully.', data, status_code=201)


@csrf_exempt
@require_admin
@require_http_methods(['GET', 'PATCH'])
def check_template_detail_api(request, template_id: int):
    if request.method == 'GET':
        try:
            data = get_template(template_id)
        except CheckTemplateError as exc:
            return _fail(exc)
        return api_success('Check template fetched successfully.', data)
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        data = update_template(template_id, body)
    except CheckTemplateError as exc:
        return _fail(exc)
    return api_success('Check template updated successfully.', data)


@csrf_exempt
@require_admin
@require_http_methods(['POST'])
def check_template_items_api(request, template_id: int):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        data = add_item(template_id, body)
    except CheckTemplateError as exc:
        return _fail(exc)
    return api_success('Check item added successfully.', data, status_code=201)


@csrf_exempt
@require_admin
@require_http_methods(['PATCH', 'DELETE'])
def check_template_item_detail_api(request, template_id: int, item_id: int):
    if request.method == 'DELETE':
        try:
            delete_item(template_id, item_id)
        except CheckTemplateError as exc:
            return _fail(exc)
        return api_success('Check item deleted successfully.', {'ref': item_id})
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        data = update_item(template_id, item_id, body)
    except CheckTemplateError as exc:
        return _fail(exc)
    return api_success('Check item updated successfully.', data)
