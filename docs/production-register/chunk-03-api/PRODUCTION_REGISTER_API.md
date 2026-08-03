# PRODUCTION_REGISTER_API.md — Chunk 3

**Status:** APPROVED  
**Date:** 2026-08-03  
**Depends on:** [ERD](../chunk-02-erd/PRODUCTION_REGISTER_ERD.md), [Requirements](../chunk-01-requirements/PRODUCTION_REGISTER_REQUIREMENTS.md)

**Mount:** `path('production/', include('production_register.urls'))`  
**Auth:** same as `/stock/` (Cognito Bearer / project token)  
**Envelope:** `{ "status": "success"|"error", "message": "...", "data": ... }`  
**Decimals:** JSON strings preferred for quantities  
**Logic:** all in `production_register/views.py` — no `services.py`

---

## 1. Endpoint map (Phase 1)

| # | Method | Path | Purpose |
|--:|--------|------|---------|
| 1 | GET | `/production/stations/` | List active stations (HR, Sleeving, …) |
| 2 | GET | `/production/stations/<code>/` | Station detail + default locations |
| 3 | GET | `/production/runs/` | List runs (`?station=&status=&date=`) |
| 4 | POST | `/production/runs/` | Create draft run (auto use-by + pin recipe version) |
| 5 | GET | `/production/runs/<run_id>/` | Run detail + consumptions |
| 6 | PATCH | `/production/runs/<run_id>/` | Update draft make fields |
| 7 | GET | `/production/runs/<run_id>/preview-consume/` | BOM lines + live lot dropdown options |
| 8 | PUT | `/production/runs/<run_id>/consumptions/` | Replace draft consumption picks |
| 9 | POST | `/production/runs/<run_id>/post/` | Post MADE + CONSUME via `stock_ledger.production()` |
| 10 | POST | `/production/runs/<run_id>/void/` | Void draft or reverse posted |
| 11 | GET | `/production/stations/<code>/downtime/` | List downtime |
| 12 | POST | `/production/stations/<code>/downtime/` | Create downtime (no stock) |
| 13 | DELETE | `/production/downtime/<id>/` | Delete downtime row |

**Out of Phase 1:** warehouse transfer (use `/stock/transfer/`), Internal Process stations seed later.

---

## 2. Shared shapes

### Station

```json
{
  "id": 1,
  "code": "high_risk",
  "name": "High Risk",
  "location_id": 4,
  "default_output_location_id": 5,
  "default_consume_location_id": 4,
  "is_active": true
}
```

### Run

```json
{
  "id": 100,
  "station_id": 1,
  "station_code": "high_risk",
  "status": "draft",
  "product_id": 501,
  "quantity_made": "34.000000",
  "unit_id": 3,
  "recipe_version_id": 88,
  "recipe_version_number": 3,
  "recipe_source": "plan_line",
  "plan_line_id": 12,
  "from_location_id": 4,
  "to_location_id": 5,
  "resource_id": 7,
  "shift": "AM",
  "start_at": "2026-08-03T08:00:00Z",
  "end_at": "2026-08-03T09:30:00Z",
  "production_date": "2026-08-03",
  "use_by": "2026-08-10",
  "trace_number": "215",
  "staff_count": 2,
  "trays": 4,
  "batches": 1,
  "idempotency_key": null,
  "output_stock_entry_id": null,
  "created_at": "2026-08-03T08:05:00Z",
  "posted_at": null,
  "voided_at": null
}
```

`recipe_source`: `"plan_line"` | `"active_latest"`

### Consumption line (stored pick)

```json
{
  "id": 1,
  "run_id": 100,
  "component_product_id": 502,
  "lot_id": 9001,
  "quantity": "12.500000",
  "unit_id": 2,
  "needed_qty": "12.500000",
  "stock_entry_id": null
}
```

### Preview BOM ingredient (not stored until PUT consumptions)

```json
{
  "component_product_id": 502,
  "component_name": "Onion Bhaji mix",
  "line_no": 1,
  "needed_qty": "12.500000",
  "unit_id": 2,
  "lots": [
    {
      "lot_id": 9001,
      "quantity_on_hand": "40.000000",
      "use_by": "2026-08-08",
      "production_date": "2026-08-01",
      "trace_number": "213",
      "location_id": 4
    }
  ]
}
```

Lots sorted FEFO (earliest `use_by` first). Only `quantity_on_hand > 0`.

### Downtime

```json
{
  "id": 1,
  "station_id": 1,
  "start_at": "2026-08-03T06:00:00Z",
  "end_at": "2026-08-03T06:00:00Z",
  "resource_id": 7,
  "shift": "AM",
  "remarks": "DAY START"
}
```

---

## 3. Endpoint contracts

### 3.1 `GET /production/stations/`

**Response `data`:** `[ Station, ... ]` active only by default; `?include_inactive=1` optional.

### 3.2 `GET /production/stations/<code>/`

**404** if unknown code.

### 3.3 `GET /production/runs/`

**Query:** `station` (code), `status`, `date` (= `production_date`), `plan_line_id`

**Response `data`:** `[ Run, ... ]` newest first; limit 200.

### 3.4 `POST /production/runs/` — create draft

**Body:**

```json
{
  "station_code": "high_risk",
  "product_id": 501,
  "quantity_made": "34",
  "unit_id": 3,
  "plan_line_id": 12,
  "from_location_id": null,
  "to_location_id": null,
  "resource_id": 7,
  "shift": "AM",
  "start_at": "2026-08-03T08:00:00Z",
  "end_at": "2026-08-03T09:30:00Z",
  "production_date": null,
  "use_by": null,
  "trace_number": null,
  "staff_count": 2,
  "trays": 4,
  "batches": 1
}
```

**Server behaviour:**

1. Resolve locations from station defaults if null.
2. **Pin recipe version (R-BOM-5):**
   - if `plan_line_id` → use `plan_line.recipe_version_id` (error if missing)
   - else → latest `ACTIVE` recipe version for product
3. Auto `production_date` / `use_by` if null (requirements §4); if product `force_*` and client omitted → **400**.
4. Status = `draft`.

**201** + Run.

### 3.5 `GET /production/runs/<run_id>/`

**Response `data`:**

```json
{
  "run": { },
  "consumptions": [ ]
}
```

### 3.6 `PATCH /production/runs/<run_id>/`

Draft only. Updatable: qty, times, resource, shift, staff/trays/batches, production_date, use_by, trace, locations (within reason).

**If `product_id` or `plan_line_id` or `quantity_made` changes:** re-pin recipe if product/plan changes; clear consumptions (client must re-preview).

**409** if not draft.

### 3.7 `GET /production/runs/<run_id>/preview-consume/`

**Response `data`:**

```json
{
  "run_id": 100,
  "recipe_version_id": 88,
  "recipe_version_number": 3,
  "process_loss": "1.0000",
  "consume_location_id": 4,
  "ingredients": [ ]
}
```

`needed_qty` = component qty × made qty × process_loss rules (match recipe/planning explode intent; document exact formula in views).

Lots from `StockBalance` (or ATP) at `consume_location_id` for each component product.

**404** if no pinned recipe / no components.

### 3.8 `PUT /production/runs/<run_id>/consumptions/`

Replace all draft picks.

**Body:**

```json
{
  "consumptions": [
    {
      "component_product_id": 502,
      "lot_id": 9001,
      "quantity": "12.5",
      "unit_id": 2
    }
  ]
}
```

**Rules:**

- Run must be draft.
- Each `component_product_id` must be on pinned recipe.
- `lot_id` must belong to that product and have stock at consume location (validate on put; re-check on post).
- Server fills `needed_qty` snapshot from current preview math.
- Multi-lot: multiple rows same `component_product_id` allowed.

**200** + `{ "run": ..., "consumptions": [...] }`

### 3.9 `POST /production/runs/<run_id>/post/`

**Body:**

```json
{
  "idempotency_key": "run-100-post-1"
}
```

**Rules:**

1. Draft only (or idempotent replay if already posted with same key → return existing).
2. Require consumptions covering each ingredient `needed_qty` (Phase 1: **block** if under).
3. Create output lot (production origin) with run’s production_date / use_by / trace.
4. Call `stock_ledger.production(idempotency_key=..., output_lot=..., output_location_id=to_location, output_quantity=quantity_made, inputs=[...])`.
5. Persist `output_stock_entry_id` + each consumption `stock_entry_id`; status `posted`; `posted_at` now.
6. If location `extends_component_use_by`: before post, clamp run `use_by` to min selected lot use_bys when earlier.

**201/200** + full run detail.

**Errors:** 400 short stock / incomplete BOM; 409 wrong status; 409 insufficient balance at post time.

### 3.10 `POST /production/runs/<run_id>/void/`

**Body (optional):** `{ "idempotency_key": "void-100-1", "reason": "..." }`

- `draft` → status void (no ledger).
- `posted` → reverse output + each consumption via `stock_ledger.reversal`; status void.
- Already void → 200 idempotent.

### 3.11–3.13 Downtime

CRUD-lite as in endpoint map. No stock side effects.

---

## 4. Error shape

```json
{
  "status": "error",
  "message": "Insufficient stock for lot 9001",
  "data": {
    "code": "INSUFFICIENT_STOCK",
    "lot_id": 9001,
    "needed": "12.500000",
    "available": "10.000000"
  }
}
```

Suggested codes: `VALIDATION`, `NOT_DRAFT`, `RECIPE_MISSING`, `PLAN_LINE_RECIPE_MISSING`, `INCOMPLETE_BOM`, `INSUFFICIENT_STOCK`, `FORCE_USE_BY_REQUIRED`.

---

## 5. Auth / idempotency

| Concern | Rule |
|---|---|
| Auth | Match stock write endpoints |
| Idempotency | Required on `post` and posted `void`; unique on run |
| No legacy SP | Views call ORM + `stock_ledger.util.services` only |

---

## 6. Acceptance criteria (Chunk 3)

- [x] Endpoint map covers Make → preview BOM lots → PUT picks → post → void
- [x] Recipe pin + `recipe_source` documented on create
- [x] Preview returns FEFO-sorted live lots for dropdown
- [x] Post contracts `stock_ledger.production()`
- [x] Ready for Chunk 4 Django schema
