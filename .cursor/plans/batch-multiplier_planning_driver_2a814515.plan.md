---
name: Batch-multiplier planning driver
overview: Add a new, self-contained, read-only planning driver that rounds the batch multiplier ONCE per process recipe (Cooking/Spice/Marination) to a fixed 0.25 increment and applies it to every ingredient line, exposed via a brand-new experimental API endpoint. The existing explode/chain-net engines and all live tables stay untouched.
todos:
  - id: driver-service
    content: "Add planning/services/explode_batchmult.py: round-once-per-recipe 0.25 multiplier, process-recipe (Cooking/Spice/Marination) detection, non-process fallback to scaled_child_net, recursive multi-level BOM with depth+cycle guards, UOM conversion, auditable per-node calc steps. No DB writes."
    status: pending
  - id: view-endpoint
    content: Add planning/views_batchmult.py with read-only POST handler mirroring views_chain_net.py (api_success/api_error, optional line_ids/increment/consider_stock), full Gazebo-style docstring.
    status: pending
  - id: url
    content: Register POST /planning/plans/<plan_id>/batch-mult/ in planning/urls.py next to chain-net.
    status: pending
  - id: verify
    content: Diff the new driver's ingredient rollup against the SIMMERS COOK workbook via the same draft-plan flow excel_compare uses; confirm process-recipe RM kg now match Excel.
    status: pending
isProject: false
---

# Batch-multiplier planning driver (experimental, read-only)

## Goal
New driver that fixes the rounding location: compute the batch multiplier **once per recipe**, `ceil(exact_batches / 0.25) * 0.25`, then multiply every ingredient by that single multiplier. Applied only to process recipes (Cooking / Spice / Marination). Everything else keeps current per-unit scaling. No changes to running planning code.

## Non-negotiables
- Do NOT edit [planning/services/explode.py](planning/services/explode.py), [planning/services/chain_net.py](planning/services/chain_net.py), [recipe/utils.py](recipe/utils.py), or any live model/table.
- Read-only: no `PlanRun` / `PlanRequirement` writes (mirror the chain-net endpoint, which is read-only per its docstring in [planning/views_chain_net.py](planning/views_chain_net.py)).
- Reuse existing adapters so the new driver reads the exact same recipe/product/stock data as production.

## Core math (new module)
For a process recipe with batch yield `BY` and required output `D`:
```
exact_batches = D / BY
multiplier    = ceil(exact_batches / 0.25) * 0.25   # ROUND_CEILING, Decimal
ingredient_qty = component.qty_per_batch * multiplier   # same multiplier for every line
```
- `BY` = `RecipeVersionSpec.batch_quantity` (BOM-sum grams-per-batch), already surfaced by [planning/adapters/recipe.py](planning/adapters/recipe.py).
- `D` = required output of that recipe, propagated from the parent explosion (FG demand at the root).
- Non-process recipes (pack/belt/fry, per-unit): fall back to existing `scaled_child_net` from [recipe/utils.py](recipe/utils.py) so their behavior matches production.
- Recurse the BOM, rounding independently at each process level (multi-level fix). Guard with `MAX_BOM_DEPTH = 20` + ancestry cycle-detection, same as the existing engines.

## Eligibility (which recipes get 0.25 rounding)
- Reuse `is_process_batch_recipe(recipe_code, name)` (covers Spice `-S`, Cook `-C`, Steam `- St`, Mix `-Mx`).
- Extend, locally in the new module, to also match **Marination** (name marker ` - Marination` / ` - Marinade`) — do NOT modify `recipe/utils.py`. Note this heuristic in the endpoint response so it can be tuned against the real plan.

## Files to add (all new)
- `planning/services/explode_batchmult.py` — the driver. `DRIVER_VERSION = 'batchmult-0.1'`. Pure functions returning a JSON tree (product line + ingredient rollup), reusing:
  - `planning.adapters.recipe` (`resolve_recipe_version_id`, `get_recipe_version`)
  - `planning.adapters.product` (`get_product_spec`)
  - `recipe.utils.scaled_child_net` (non-process fallback only)
  - `stock_ledger.util.conversions.to_product_unit` (UOM g/kg, same as explode's `_convert_bom_to_stock`)
  - optional stock netting via `planning.services.netting` behind a request flag (default off for clean experimentation).
  - Emit per-node `calc_json`-style steps (exact_batches, multiplier, per-ingredient) so results are auditable and diffable against Excel.
- `planning/views_batchmult.py` — `POST /planning/plans/<plan_id>/batch-mult/`, read-only, mirrors [planning/views_chain_net.py](planning/views_chain_net.py): `csrf_exempt`, `require_http_methods(['POST'])`, `api_success`/`api_error`, optional `line_ids`, `increment` (default `0.25`), `consider_stock` (default false). Full Gazebo-style docstring per postman-docs rule.
- Register the route in [planning/urls.py](planning/urls.py) next to the existing `chain-net` path.

## Verification
- Point the new endpoint at the same draft plan used by [planning/services/excel_compare.py](planning/services/excel_compare.py) and diff its ingredient rollup against the `PART 1 WC20 SEPT SIMMERS COOK` workbook RM kg. Success = process-recipe raw materials line up with Excel (which rounds to fractional batches) where the current `explode-1.4` under-states them.

## Later (explicitly deferred)
- Wiring this into the live plan (persisting a real `PlanRun` with `driver_version='batchmult-…'`) is a follow-up once the numbers match Excel. Not in this pass.