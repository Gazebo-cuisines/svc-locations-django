"""HTTP for the experimental batch-multiplier driver. Read-only.

Does not write PlanRun / PlanRequirement or touch the live explode engine.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from planning.errors import PlanningError
from planning.models import Plan
from planning.services import explode_batchmult


def _parse_json_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if data is None:
        return {}
    if not isinstance(data, dict):
        return None
    return data


@csrf_exempt
@require_http_methods(['POST'])
def plan_batchmult_api(request, plan_id: int):
    """
    ### Batch-multiplier explode (experimental)

    `POST /planning/plans/:plan_id/batch-mult/`

    Read-only BOM explosion that reproduces the day-plan workbook. Raw
    materials scale on the **exact** yield-adjusted output (matching Excel's
    Fresh Products sheet); the batch count is rounded for the **production
    schedule only** and reported separately per recipe node
    (`production_batches` / `production_output`), never applied to ingredient
    quantities. Two production regimes:

    - **Cook / process** (recipe has `batch_quantity`): whole batches on the
      raw recipe weight per batch, `ceil((demand / process_loss) / batch_quantity)`.
    - **Belt / folding** (`One Mix Qty` from `ResourceProductRate.batch_size`):
      fractional batches to the `increment` (default 0.25),
      `ceil(demand / one_mix_qty / increment) * increment`.
    - **Per-unit** otherwise: no batch rounding.

    Does **not** create a PlanRun or change the standard explode. Nothing is
    persisted.

    #### Path parameters

    | Name | Required | Description |
    |------|----------|-------------|
    | plan_id | yes | Plan primary key |

    #### Query parameters

    None.

    #### Request body

    Optional JSON object:

    | Field | Required | Description |
    |-------|----------|-------------|
    | line_ids | no | List of plan_line ids to include; omit = all lines |
    | increment | no | Fractional batch increment for the belt regime (default `0.25`) |
    | consider_stock | no | Net demand against eligible stock before batching (default `false`) |

    #### Response body

    Success payload includes `plan_id`, `plan_date`, `driver_version`,
    `increment`, `consider_stock`, `items[]` (per demand line: the full BOM
    tree with `regime`, exact `effective_output`, production-only
    `production_batches` / `production_output` / `batch_yield`, and a
    plain-English `explanation` on every recipe node), and `ingredients[]`
    (raw-material rollup with `quantity`, `unit_name`, and `kg` for diffing
    against the Fresh Products sheets).

    #### Status codes

    | Code | When |
    |------|------|
    | 200 | Explosion calculated |
    | 400 | Invalid JSON / fields |
    | 404 | Plan not found |
    | 422 | Planning validation (e.g. no lines, BOM cycle, depth exceeded) |
    """
    if not Plan.objects.filter(pk=plan_id).exists():
        return api_error('Plan not found.', status_code=404)

    body = _parse_json_body(request)
    if body is None:
        return api_error('Invalid JSON body.')

    line_ids = body.get('line_ids')
    if line_ids is not None:
        if not isinstance(line_ids, list) or not all(
            isinstance(x, int) and not isinstance(x, bool) for x in line_ids
        ):
            return api_error('line_ids must be a list of integers.')

    increment = None
    if body.get('increment') not in (None, ''):
        try:
            increment = Decimal(str(body['increment']))
        except (InvalidOperation, TypeError, ValueError):
            return api_error('increment must be a decimal.')
        if increment <= 0:
            return api_error('increment must be > 0.')

    consider_stock = bool(body.get('consider_stock', False))

    try:
        data = explode_batchmult.run_batchmult_plan(
            plan_id,
            line_ids=line_ids,
            increment=increment,
            consider_stock=consider_stock,
        )
    except PlanningError as exc:
        msg = str(exc)
        if 'not found' in msg.lower():
            return api_error(msg, status_code=404)
        return api_error(msg, status_code=422)

    return api_success('Batch-multiplier explode calculated.', data)
