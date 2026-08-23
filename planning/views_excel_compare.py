from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from planning.errors import PlanningError, PlanningStateError
from planning.services.excel_compare import (
    ExcelCompareError,
    public_result,
    run_excel_compare,
)

_TRUE = {'1', 'true', 'yes'}


@csrf_exempt
@require_http_methods(['POST'])
def excel_compare_api(request):
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

    qty_mode = (request.POST.get('qty_mode') or 'packs').strip().lower()
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
        )
    except ExcelCompareError as exc:
        return api_error(str(exc))
    except (PlanningError, PlanningStateError) as exc:
        status = 409 if isinstance(exc, PlanningStateError) else 422
        return api_error(str(exc), status_code=status)

    message = (
        'Excel plan compared (dry run).'
        if dry_run
        else 'Excel plan exploded and compared.'
    )
    return api_success(
        message,
        public_result(result),
        status_code=200 if dry_run else 201,
    )
