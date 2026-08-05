"""HTTP for chain-net. Read-only — does not write PlanRun."""

from __future__ import annotations

import json

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from locations.utils.api_response import api_error, api_success
from planning.errors import PlanningError
from planning.models import Plan
from planning.services import chain_net


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


def _as_demand_dict(value, field_name: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f'{field_name} must be an object')
    return value


@csrf_exempt
@require_http_methods(['POST'])
def plan_chain_net_api(request, plan_id: int):
    """
    ### Chain-net (backward stock plan)

    `POST /planning/plans/:plan_id/chain-net/`

    Read-only backward stock netting for a plan's demand lines. Walks the BOM
    from finished good toward Unit 2, applying demand composition (pending
    despatch, WIP, manual make), stage stock, and recipe min-batch.
    Does **not** create a PlanRun or change standard explode.

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
    | line_ids | no | List of plan_line ids; omit = all lines |
    | demand | no | Default composition for all lines (see below) |
    | line_demand | no | Map of plan_line_id → composition overrides |

    `demand` / per-line object fields:

    | Field | Required | Description |
    |-------|----------|-------------|
    | demand_source | no | `manual` (default) or `sales_order` (future stub) |
    | manual_make_qty | no | Override target make qty |
    | today_pending_dispatch_qty | no | Still owed for today's despatch (default 0) |
    | wip_fg_equivalent_qty | no | In-process qty that will become this FG (default 0) |

    #### Response body

    Success payload includes `plan_id`, `plan_date`, `items[]` (full tree with
    `demand_breakdown` on FG roots), `product_lines[]` (recipe/process tab;
    includes `unit_name`, `stock_lots`, `stock_by_location`), and
    `ingredients[]` (materials tab: quantity/stock/balance/cost/supplier/
    `stock_lots`).

    #### Status codes

    | Code | When |
    |------|------|
    | 200 | Chain-net calculated |
    | 400 | Invalid JSON / fields |
    | 404 | Plan not found |
    | 422 | Planning validation (e.g. no lines, BOM cycle) |
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

    try:
        demand_inputs = _as_demand_dict(body.get('demand'), 'demand')
        raw_line_demand = _as_demand_dict(body.get('line_demand'), 'line_demand')
        line_demand = None
        if raw_line_demand is not None:
            line_demand = {}
            for key, value in raw_line_demand.items():
                try:
                    line_id = int(key)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        'line_demand keys must be plan_line ids'
                    ) from exc
                if not isinstance(value, dict):
                    raise ValueError(
                        f'line_demand[{key}] must be an object'
                    )
                line_demand[line_id] = value

        data = chain_net.chain_net_plan(
            plan_id,
            line_ids=line_ids,
            demand_inputs=demand_inputs,
            line_demand=line_demand,
        )
    except ValueError as exc:
        return api_error(str(exc), status_code=400)
    except PlanningError as exc:
        msg = str(exc)
        if 'not found' in msg.lower():
            return api_error(msg, status_code=404)
        return api_error(msg, status_code=422)

    return api_success('Chain-net calculated.', data)
