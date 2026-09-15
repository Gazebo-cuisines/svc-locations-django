import json

from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from planning.errors import PlanningError, PlanningStateError
from planning.models import ExcelCompareReport
from planning.services.excel_compare import (
    DRIVER_BATCHMULT,
    DRIVER_EXPLODE,
    DRIVERS,
    ExcelCompareError,
    persist_report,
    report_detail,
    report_summary,
    rerun_excel_compare,
    run_excel_compare,
)

_TRUE = {'1', 'true', 'yes'}


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def excel_compare_api(request):
    if request.method == 'GET':
        qs = ExcelCompareReport.objects.all()
        location_id = request.GET.get('location_id')
        if location_id:
            try:
                qs = qs.filter(location_id=int(location_id))
            except (TypeError, ValueError):
                return api_error('location_id must be an integer.')
        total = qs.count()
        items = [report_summary(row) for row in qs[:50]]
        return api_success(
            'Excel compare reports listed.',
            {'items': items, 'count': total},
        )

    uploaded = request.FILES.get('file')
    if not uploaded:
        return api_error('Excel file is required (multipart field: file).')
    name = (uploaded.name or '').lower()
    if not name.endswith(('.xlsx', '.xlsm', '.xls')):
        return api_error('File must be .xlsx or .xlsm.')

    location_raw = request.POST.get('location_id')
    date_raw = request.POST.get('plan_date')
    if not location_raw or not date_raw:
        return api_error('location_id and plan_date are required.')
    try:
        location_id = int(location_raw)
    except (TypeError, ValueError):
        return api_error('location_id must be an integer.')
    plan_date = parse_date(date_raw)
    if plan_date is None:
        return api_error('plan_date must be YYYY-MM-DD.')

    qty_mode = (request.POST.get('qty_mode') or 'cases').strip().lower()
    driver = (request.POST.get('driver') or DRIVER_EXPLODE).strip().lower()
    dry_run = str(request.POST.get('dry_run') or '').lower() in _TRUE
    plan_id_raw = request.POST.get('plan_id')
    plan_id = None
    if plan_id_raw not in (None, ''):
        try:
            plan_id = int(plan_id_raw)
        except (TypeError, ValueError):
            return api_error('plan_id must be an integer.')

    try:
        result = run_excel_compare(
            source=uploaded,
            location_id=location_id,
            plan_date=plan_date,
            qty_mode=qty_mode,
            dry_run=dry_run,
            plan_id=plan_id,
            remarks=uploaded.name,
            driver=driver,
        )
    except ExcelCompareError as exc:
        return api_error(str(exc))
    except (PlanningError, PlanningStateError) as exc:
        status = 409 if isinstance(exc, PlanningStateError) else 422
        return api_error(str(exc), status_code=status)

    pub = persist_report(result, uploaded.name)
    message = (
        'Excel plan compared (dry run).'
        if dry_run
        else 'Excel plan exploded and compared.'
    )
    return api_success(
        message,
        pub,
        status_code=200 if dry_run else 201,
    )


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def excel_compare_report_api(request, report_id):
    """
    ### Excel compare report

    `GET /planning/excel-compare/:report_id/`

    Returns the stored Excel vs system compare (finished goods, RM, explode audit).

    `POST /planning/excel-compare/:report_id/`

    Re-explodes the draft plan already on this report with another driver
    (default `batchmult`). Excel kilograms stay as uploaded; only system RM
    and the explode audit are rebuilt. Writes a new report and returns it.

    #### Path parameters

    | Name | Required | Description |
    |------|----------|-------------|
    | report_id | yes | ExcelCompareReport primary key |

    #### Query parameters

    None.

    #### Request body

    Optional JSON (POST only):

    | Field | Required | Description |
    |-------|----------|-------------|
    | driver | no | `batchmult` (default) or `explode` |

    #### Response body

    Same shape as a new compare: `report_id`, `plan_id`, `driver`,
    `driver_version`, `finished_goods`, `rm_compare`, `system_only`,
    `explode_audit`.

    #### Status codes

    | Code | When |
    |------|------|
    | 200 | GET retrieved |
    | 201 | POST re-ran and saved a new report |
    | 400 | Invalid driver / JSON |
    | 404 | Report not found |
    | 422 | Dry-run report (no plan) or explode failed |
    """
    row = ExcelCompareReport.objects.filter(pk=report_id).first()
    if not row:
        return api_error('Excel compare report not found.', status_code=404)
    if request.method == 'GET':
        return api_success('Excel compare report retrieved.', report_detail(row))

    driver = DRIVER_BATCHMULT
    if request.body:
        try:
            body = json.loads(request.body.decode('utf-8') or '{}')
        except (json.JSONDecodeError, UnicodeDecodeError):
            return api_error('Invalid JSON body.')
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return api_error('Invalid JSON body.')
        if body.get('driver') not in (None, ''):
            driver = str(body['driver']).strip().lower()
    if driver not in DRIVERS:
        return api_error('driver must be explode or batchmult.')
    try:
        pub = rerun_excel_compare(row, driver)
    except ExcelCompareError as exc:
        return api_error(str(exc), status_code=422)
    except (PlanningError, PlanningStateError) as exc:
        status = 409 if isinstance(exc, PlanningStateError) else 422
        return api_error(str(exc), status_code=status)
    return api_success('Excel plan re-exploded and compared.', pub, status_code=201)
