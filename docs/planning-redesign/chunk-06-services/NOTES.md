# Chunk 6 — MRP services

**Branch:** `Plan`  
**Code:** `svc-locations-django/planning/adapters/` + `planning/services/`

## Adapters (`CONTRACT_VERSION = planning-adapters-1.0`)

| Module | Role |
|--------|------|
| `adapters/product.py` | ProductSpec (yield, flags, batch size, shelf life) |
| `adapters/recipe.py` | Pin/resolve recipe version + components |
| `adapters/locations.py` | `plannable_locations()`, location min shelf life |
| `adapters/stock.py` | Lots/ATP, projected supply, soft-alloc totals, reserve/release |

## Services

| Module | Entry points |
|--------|----------------|
| `explode.py` | `run_explode(plan_id)` — select_for_update, new `plan_run`, BOM + netting + batching |
| `eligibility.py` | FEFO + min remaining shelf life |
| `netting.py` | ATP − soft allocs + `plan_supply` as-of plan date |
| `batching.py` | Full-batch split + last-batch align |
| `allocate.py` | `soft_allocate`, `commit_plan_allocations`, `delete_soft_allocation` |
| `lifecycle.py` | `create_plan`, `lock_plan`, `close_plan`, `reopen_plan`, `rollover_open_lines` |

HTTP wiring is **Chunk 7**.
