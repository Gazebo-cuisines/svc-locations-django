import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from purchasing.serialize import po_detail_dict, po_list_dict
from purchasing.services.attachments import (
    AttachmentError,
    delete_attachment,
    list_attachments,
    upload_attachment,
)
from purchasing.services.goods_in_form import (
    GoodsInFormError,
    resolve_goods_in_form,
)
from purchasing.services.header_qc import HeaderQcError, submit_header_qc
from purchasing.services.legacy_csv import LegacyCsvError, import_legacy_csv
from purchasing.services.line_qc import LineQcError, submit_line_qc
from purchasing.services.print_pdf import pdf_http_response
from purchasing.services.receive import ReceiveError, receive_purchase_order
from purchasing.services.release import ReleaseError, release_from_quarantine
from purchasing.services.po import (
    PoValidationError,
    create_purchase_order,
    get_purchase_order,
    list_purchase_orders,
    update_purchase_order,
)
from purchasing.models import GoodsInAttachmentKind, PurchaseOrder, PurchaseOrderStatus
from users_rbac.auth import attach_user
from users_rbac.permissions import gate_warehouse_write


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
                sage_po_number=(
                    request.GET.get('sage_po_number')
                    or request.GET.get('external_number')
                ),
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
            sage_po_number=body.get('sage_po_number'),
            external_number=body.get('external_number'),
            require_sage_po_number=True,
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


@csrf_exempt
@require_http_methods(['GET'])
def po_goods_in_form_api(request, po_id: int):
    try:
        data = resolve_goods_in_form(po_id)
    except GoodsInFormError as exc:
        msg = str(exc)
        status = 404 if msg == 'Purchase order not found.' else 400
        return api_error(msg, status_code=status)
    return api_success('Goods inward form resolved successfully.', data)


@csrf_exempt
@require_http_methods(['POST'])
def po_header_qc_api(request, po_id: int):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        data = submit_header_qc(po_id, body=body)
    except HeaderQcError as exc:
        msg = str(exc)
        status = 404 if msg == 'Purchase order not found.' else 400
        return api_error(msg, status_code=status)
    return api_success('Header QC saved successfully.', data)


@csrf_exempt
@require_http_methods(['POST'])
def po_line_qc_api(request, po_id: int, line_id: int):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        data = submit_line_qc(po_id, line_id, body=body)
    except LineQcError as exc:
        msg = str(exc)
        if msg in (
            'Purchase order not found.',
            'Purchase order line not found.',
        ):
            status = 404
        else:
            status = 400
        return api_error(msg, status_code=status)
    return api_success('Line QC saved successfully.', data)


@csrf_exempt
@require_http_methods(['POST'])
@gate_warehouse_write(action='goods_in')
def po_receive_api(request, po_id: int):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        # Reuse stock receipt audit helpers (JWT / workstation / IP).
        from stock_ledger.views import _common_write_kwargs

        audit = _common_write_kwargs(request, body)
        data = receive_purchase_order(po_id, body=body, audit=audit)
    except ReceiveError as exc:
        msg = str(exc)
        status = 404 if msg == 'Purchase order not found.' else 400
        return api_error(msg, status_code=status)
    return api_success('Goods received successfully.', data, status_code=201)


@csrf_exempt
@require_http_methods(['POST'])
def po_release_api(request, po_id: int):
    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.', status_code=400)
    try:
        data = release_from_quarantine(po_id, body=body)
    except ReleaseError as exc:
        msg = str(exc)
        status = 404 if msg == 'Purchase order not found.' else 400
        return api_error(msg, status_code=status)
    return api_success('Quarantine stock released successfully.', data)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def po_attachments_api(request, po_id: int):
    if request.method == 'GET':
        try:
            data = list_attachments(po_id)
        except AttachmentError as exc:
            msg = str(exc)
            status = 404 if msg == 'Purchase order not found.' else 400
            return api_error(msg, status_code=status)
        return api_success(
            'Attachments fetched successfully.',
            {'count': len(data), 'results': data},
        )

    uploaded = request.FILES.get('file') or request.FILES.get('image')
    if not uploaded:
        return api_error('File is required (multipart field: file).', status_code=400)

    kind = request.POST.get('kind') or GoodsInAttachmentKind.PHOTO
    uploaded_by = request.POST.get('uploaded_by_user_id')
    if uploaded_by not in (None, ''):
        try:
            uploaded_by = int(uploaded_by)
        except (TypeError, ValueError):
            return api_error('uploaded_by_user_id must be an integer.', status_code=400)
    else:
        uploaded_by = None
        # Prefer Cognito actor when FE sends Authorization Bearer JWT
        attach_user(request, missing='ok', invalid='ok')
        actor = getattr(request, 'rbac_user', None)
        if actor is not None:
            uploaded_by = actor.id

    try:
        data = upload_attachment(
            po_id,
            uploaded_file=uploaded,
            kind=kind,
            line_id=request.POST.get('line_id'),
            history_id=request.POST.get('history_id'),
            uploaded_by_user_id=uploaded_by,
        )
    except AttachmentError as exc:
        msg = str(exc)
        status = 404 if msg == 'Purchase order not found.' else 400
        return api_error(msg, status_code=status)
    return api_success('Attachment uploaded successfully.', data, status_code=201)


@csrf_exempt
@require_http_methods(['DELETE'])
def po_attachment_detail_api(request, po_id: int, attachment_id: int):
    try:
        delete_attachment(po_id, attachment_id)
    except AttachmentError as exc:
        msg = str(exc)
        status = 404 if 'not found' in msg.lower() else 400
        return api_error(msg, status_code=status)
    return api_success('Attachment deleted successfully.', data=None)


@csrf_exempt
@require_http_methods(['POST'])
def legacy_csv_import_api(request):
    uploaded = request.FILES.get('file') or request.FILES.get('csv')
    if not uploaded:
        return api_error(
            'CSV file is required (multipart field: file).',
            status_code=400,
        )
    dry_run = str(request.POST.get('dry_run') or request.GET.get('dry_run') or '')
    dry_run = dry_run.lower() in ('1', 'true', 'yes')
    status = request.POST.get('status') or PurchaseOrderStatus.ORDERED
    created_by = request.POST.get('created_by_user_id')
    if created_by not in (None, ''):
        try:
            created_by = int(created_by)
        except (TypeError, ValueError):
            return api_error('created_by_user_id must be an integer.', status_code=400)
    else:
        created_by = None

    try:
        data = import_legacy_csv(
            file_bytes=uploaded.read(),
            dry_run=dry_run,
            status=status,
            created_by_user_id=created_by,
        )
    except LegacyCsvError as exc:
        return api_error(str(exc), status_code=400)
    except PoValidationError as exc:
        return api_error(str(exc), status_code=400)

    message = (
        'Legacy CSV validated successfully (dry run).'
        if dry_run
        else 'Legacy CSV imported successfully.'
    )
    return api_success(message, data, status_code=200 if dry_run else 201)


@csrf_exempt
@require_http_methods(['GET'])
def po_print_api(request, po_id: int):
    try:
        return pdf_http_response(po_id)
    except GoodsInFormError as exc:
        return api_error(str(exc), status_code=404)