"""HTTP for the batch-multiplier driver (Plan B).

Batch mult owns the BOM math; chain-net's stock, supplier and cost data is
layered on for display only. Read-only by default. With ``persist=true`` it
writes a PlanRun / PlanRequirement tree tagged
``driver_version='batchmult-0.2'``, isolated from production runs by the tag;
it never touches the live explode engine.
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


def _as_demand_dict(value, field_name: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f'{field_name} must be an object')
    return value


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
    ### Batch-multiplier explode (Plan B)

    `POST /planning/plans/:plan_id/batch-mult/`

    BOM explosion that reproduces the day-plan workbook, with the chain-net
    stock picture layered on top. Raw materials scale on the **exact**
    yield-adjusted output (matching Excel's Fresh Products sheet); the batch
    count is rounded for the **production schedule only** and reported
    separately per recipe node (`production_batches` / `production_output`),
    never applied to ingredient quantities. Three production regimes:

    - **Cook / process** (recipe has `batch_quantity`): whole batches on the
      raw recipe weight per batch, `ceil((demand / process_loss) / batch_quantity)`.
    - **Belt / folding** (`One Mix Qty` from `ResourceProductRate.batch_size`):
      fractional batches to the `increment` (default 0.25),
      `ceil(demand / one_mix_qty / increment) * increment`.
    - **Per-unit** otherwise: no batch rounding.

    Stock, supplier and cost data on every node and ingredient are **display
    only** and never reduce the requirement; `demand_breakdown` on each FG root
    is likewise informational. Read-only unless `persist` is set.

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
    | demand | no | Default demand composition applied to every line (see below) |
    | line_demand | no | Map of plan_line_id to composition overrides |
    | persist | no | Save a PlanRun + PlanRequirement tree tagged `batchmult-0.2` (default `false`) |
    | actor_name | no | Stamp for the run (default `System Admin`); only used with `persist` |

    `demand` / per-line object fields:

    | Field | Required | Description |
    |-------|----------|-------------|
    | demand_source | no | `manual` (default) or `sales_order` (future stub) |
    | manual_make_qty | no | Overrides the plan line quantity as the explode target |
    | today_pending_dispatch_qty | no | Reported in `demand_breakdown`; does not change RM |
    | wip_fg_equivalent_qty | no | Reported in `demand_breakdown`; does not change RM |

    #### Response body

    Success payload includes `plan_id`, `plan_date`, `driver_version`,
    `increment`, `consider_stock`, `persisted`, `run` (`run_id` / `run_number`
    / `status` when persisted, else `null`), `items[]` (per demand line: the
    full BOM tree with `regime`, exact `effective_output`, production-only
    `production_batches` / `production_output` / `batch_yield`, `stock` /
    `shortfall` / `stock_lots`, a plain-English `explanation`, `plan_quantity`,
    `demand_breakdown`, and `requirement_id` when persisted),
    `product_lines[]` (recipe nodes flattened for the To make tab), and
    `ingredients[]` (raw-material rollup with whole-gram `quantity`, `kg`,
    `stock`, `balance`, `stock_status`, `supplier`, `unit_cost` /
    `material_cost` and `stock_lots`).

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
    persist = bool(body.get('persist', False))

    actor_name = body.get('actor_name')
    if actor_name is not None and not isinstance(actor_name, str):
        return api_error('actor_name must be a string.')

    try:
        demand_inputs = _as_demand_dict(body.get('demand'), 'demand')
        raw_line_demand = _as_demand_dict(body.get('line_demand'), 'line_demand')
        line_demand = None
        if raw_line_demand is not None:
            line_demand = {}
            for key, value in raw_line_demand.items():
                try:
                    line_id = int(key)
                except (TypeError, ValueError):
                    return api_error('line_demand keys must be plan_line ids')
                if not isinstance(value, dict):
                    return api_error(f'line_demand[{key}] must be an object')
                line_demand[line_id] = value
    except ValueError as exc:
        return api_error(str(exc))

    try:
        data = explode_batchmult.run_batchmult_plan(
            plan_id,
            line_ids=line_ids,
            increment=increment,
            consider_stock=consider_stock,
            persist=persist,
            actor_name=actor_name,
            demand_inputs=demand_inputs,
            line_demand=line_demand,
        )
    except PlanningError as exc:
        msg = str(exc)
        if 'not found' in msg.lower():
            return api_error(msg, status_code=404)
        return api_error(msg, status_code=422)

    return api_success('Batch-multiplier explode calculated.', data)
