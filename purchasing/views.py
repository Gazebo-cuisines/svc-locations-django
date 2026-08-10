import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from purchasing.serialize import po_detail_dict, po_list_dict
from purchasing.services.po import (
    PoValidationError,
    create_purchase_order,
    get_purchase_order,
    list_purchase_orders,
    update_purchase_order,
)
from purchasing.models import PurchaseOrder


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def po_collection_api(request):
    if request.method == 'GET':
        try:
            rows = list_purchase_orders(
                status=request.GET.get('status'),
                supplier_id=request.GET.get('supplier_id'),
            )
        except (TypeError, ValueError) as exc:
            return api_error(str(exc), status_code=400)
        return api_success(
            'Purchase order list fetched successfully.',
            {
                'count': rows.count(),
                'results': [po_list_dict(po) for po in rows],
            },
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    supplier_id = body.get('supplier_id')
    if supplier_id in (None, ''):
        return api_error('Missing required field: supplier_id', status_code=400)

    try:
        po = create_purchase_order(
            supplier_id=int(supplier_id),
            lines=body.get('lines') or [],
            ship_to_location_id=body.get('ship_to_location_id'),
            expected_at=body.get('expected_at'),
            ordered_at=body.get('ordered_at'),
            remarks=body.get('remarks'),
            created_by_user_id=body.get('created_by_user_id'),
            status=body.get('status') or 'draft',
        )
    except PoValidationError as exc:
        return api_error(str(exc), status_code=400)
    except (TypeError, ValueError) as exc:
        return api_error(str(exc), status_code=400)

    return api_success(
        'Purchase order created successfully.',
        po_detail_dict(po),
        status_code=201,
    )


@csrf_exempt
@require_http_methods(['GET', 'PATCH'])
def po_detail_api(request, po_id: int):
    if request.method == 'GET':
        try:
            po = get_purchase_order(po_id)
        except PurchaseOrder.DoesNotExist:
            return api_error('Purchase order not found.', status_code=404)
        return api_success(
            'Purchase order fetched successfully.',
            po_detail_dict(po),
        )

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)

    try:
        po = update_purchase_order(po_id, body=body)
    except PoValidationError as exc:
        msg = str(exc)
        status = 404 if msg == 'Purchase order not found.' else 400
        return api_error(msg, status_code=status)

    return api_success('Purchase order updated successfully.', po_detail_dict(po))
