---
name: Batch-multiplier planning driver
overview: Add a new, self-contained, read-only planning driver that reproduces the Excel batch-rounding for BOTH regimes (belt/folding 0.25 rounding and Ready-Meals cook whole-batch + yield), category-routed, and rolls up raw-material demand so it can be diffed against the SIMMERS COOK workbook. The live explode/chain-net engines and all tables stay untouched.
todos:
  - id: driver-service
    content: "Add planning/services/explode_batchmult.py: category-routed batching (belt 0.25 ROUNDUP on batch_size; ready-meals whole-batch + yield), round-once-per-recipe, recursive multi-level BOM with depth+cycle guards, UOM conversion, RM rollup, auditable calc steps. No DB writes."
    status: completed
  - id: view-endpoint
    content: Add planning/views_batchmult.py with read-only POST handler mirroring views_chain_net.py (api_success/api_error, optional line_ids/increment/consider_stock), full Gazebo-style docstring.
    status: completed
  - id: url
    content: Register POST /planning/plans/<plan_id>/batch-mult/ in planning/urls.py next to chain-net.
    status: completed
  - id: verify
    content: Diff the new driver's ingredient rollup against the SIMMERS COOK workbook (reuse excel_compare's draft-plan + RM parsing); confirm RM kg match Excel where explode-1.4 diverges.
    status: completed
isProject: false
---

# Batch-multiplier planning driver (experimental, read-only)

## What the Excel actually does (reverse-engineered from `PART 1 WC20 SEPT SIMMERS COOK`)
Two distinct batching regimes, routed by `Category`:

- Belt/folding SKUs (`Requirement` sheet): `Number of Batches = ROUNDUP(demand_units / one_mix_qty / 0.25, 0) * 0.25`, then `Units To Produce = one_mix_qty * batches`. `one_mix_qty` = column Q "One Mix Qty / Qty Per Batch".
- Ready Meals / cooked Simmers items: the 0.25 formula is inert (`one_mix_qty = 1`). Real batching comes from `Ready Meals New` / `Ready Meals Stock`: yield % (`R`), cooked weight (`S=ROUND(...)`), `Total Cooked Weight Required`, whole `Batches Required (X)`. `Requirement!AM` switches to `Number Of Batches Ready Meals` when `Category` is `Ready Meals` / `Ready Meals-P`.

DB field mapping (verified):
- `one_mix_qty` -> `planning.models.ResourceProductRate.batch_size` (help text: "One Mix Qty - units per mixer batch (CONTENT CODES col O)"). Per (resource, product, staff_count); for this driver we ignore resource/staff and take the product's batch_size (any active row).
- yield % -> `product.ProductYield.yield_factor` (+ `chilling_loss_factor`); recipe `process_loss` on `RecipeVersion`.
- per-batch raw recipe weight -> `RecipeVersion.batch_quantity` (BOM-sum) via [planning/adapters/recipe.py](planning/adapters/recipe.py).
- category -> `Product.category` (route regime; needs surfacing into the driver, not currently on `ProductSpec`).

## Decisions locked with user
- Model BOTH regimes in one driver, category-routed.
- Ignore resource + staff_count when reading `batch_size`; focus output on raw materials.
- Increment fixed at `0.25`.
- Separate experimental API, new version, live planning untouched.

## Non-negotiables
- Do NOT edit [planning/services/explode.py](planning/services/explode.py), [planning/services/chain_net.py](planning/services/chain_net.py), [recipe/utils.py](recipe/utils.py), or any live model/table.
- Read-only: no `PlanRun` / `PlanRequirement` writes (mirror [planning/views_chain_net.py](planning/views_chain_net.py)).
- Reuse existing adapters so the driver reads the same recipe/product/stock data as production.

## Core math (new module)
Round ONCE per recipe, then apply one multiplier to every ingredient line:
```
# belt/folding regime
batches   = ceil( (demand_units / batch_size) / 0.25 ) * 0.25    # ROUND_CEILING, Decimal
units     = batch_size * batches
# ready-meals regime
gross     = net / process_loss                                   # yield-adjusted
batches   = ceil( gross / batch_quantity )                       # whole batches
# both: explode BOM with the single per-recipe batches/units, never per-ingredient rounding
ingredient_qty = component.qty_per_batch * batches               # (regime-appropriate multiplier)
```
- `D` (demand) propagates from parent explosion; FG demand at the root.
- Recurse the BOM, rounding independently at each process level (the multi-level fix). Guard with `MAX_BOM_DEPTH = 20` + ancestry cycle-detection.
- Non-batch leaf raw materials: per-unit scale (reuse `scaled_child_net` from [recipe/utils.py](recipe/utils.py)).
- Convert BOM-line units to stock units via `stock_ledger.util.conversions.to_product_unit` (same as explode's `_convert_bom_to_stock`).

## Regime routing
- Read `Product.category` (+ recipe classification `is_process_batch_recipe`) inside the driver.
- `Ready Meals` / `Ready Meals-P` -> ready-meals cook regime.
- Otherwise -> belt/folding 0.25 regime (falls back to per-unit when no `batch_size`).
- Emit the chosen regime + inputs per node so results are explainable and tunable.

## Files to add (all new)
- `planning/services/explode_batchmult.py` - the driver. `DRIVER_VERSION = 'batchmult-0.1'`. Pure functions returning a JSON tree + `ingredients[]` rollup (product_id, kg, source regime, batches), reusing `planning.adapters.recipe`, `planning.adapters.product`, `ResourceProductRate` (batch_size lookup), `recipe.utils.scaled_child_net`, `stock_ledger.util.conversions.to_product_unit`. Optional stock netting via `planning.services.netting` behind a request flag (default off).
- `planning/views_batchmult.py` - `POST /planning/plans/<plan_id>/batch-mult/`, read-only, mirrors [planning/views_chain_net.py](planning/views_chain_net.py): `csrf_exempt`, `require_http_methods(['POST'])`, `api_success`/`api_error`, optional `line_ids`, `increment` (default `0.25`), `consider_stock` (default false). Full Gazebo-style docstring per postman-docs rule.
- Register the route in [planning/urls.py](planning/urls.py) next to the existing `chain-net` path.

## Verification
- Reuse [planning/services/excel_compare.py](planning/services/excel_compare.py) parsing (`parse_workbook`, Fresh Products RM rows) but diff against the NEW driver's `ingredients[]` instead of `explode-1.4`. Success = RM kg for belt AND ready-meals items line up with the workbook where the current engine diverges.
- Sanity spot-checks against known rows: belt samosa SKUs (Q>1) and Simmers items (`GZRM381` Cacciatore, `GZRM383` Steamed Green Beans, `GZRM385` Thai Style Rice).

## Later (explicitly deferred)
- Picking resource/staff-specific `batch_size`, scheduling, and persisting a real `PlanRun` (`driver_version='batchmult-...'`) - only after RM numbers match Excel.
