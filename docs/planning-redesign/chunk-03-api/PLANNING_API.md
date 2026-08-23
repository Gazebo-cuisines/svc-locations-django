# PLANNING_API.md — Chunk 3 (Signed)

**Status:** COMPLETE  
**Date:** 2026-07-31  
**Folder:** `planning-redesign/chunk-03-api/`  
**Depends on:** [ERD](../chunk-02-erd/PLANNING_ERD.md), [User journey](../USER_JOURNEY_CREATE_PLAN.md)

**Mount:** `path('planning/', include('planning.urls'))` in Django  
**Auth:** Cognito Bearer (same as product/recipe/stock)  
**Envelope:** `{ "status": "success"|"error", "message": "...", "data": ... }` via `api_success` / `api_error`  
**Decimals:** JSON strings preferred for quantities (e.g. `"100.000000"`) to avoid float drift — accept number too on input.

---

## 1. Endpoint map (MVP)

| # | Method | Path | Purpose | Journey step |
|--:|--------|------|---------|--------------|
| 1 | GET | `/planning/plans/` | List plans | — |
| 2 | POST | `/planning/plans/` | Create plan | Step 1 |
| 3 | GET | `/planning/plans/<plan_id>/` | Plan detail (+ lines summary) | — |
| 4 | PATCH | `/planning/plans/<plan_id>/` | Update remarks (draft only) | — |
| 5 | POST | `/planning/plans/<plan_id>/lock/` | draft → locked | Step 6 |
| 6 | POST | `/planning/plans/<plan_id>/close/` | → closed | End of day |
| 7 | POST | `/planning/plans/<plan_id>/reopen/` | closed → draft | — |
| 8 | GET | `/planning/plans/<plan_id>/lines/` | List lines | Step 2 |
| 9 | POST | `/planning/plans/<plan_id>/lines/` | Add line | Step 2 |
| 10 | PATCH | `/planning/plans/<plan_id>/lines/<line_id>/` | Edit line (draft) | — |
| 11 | DELETE | `/planning/plans/<plan_id>/lines/<line_id>/` | Delete line (draft) | — |
| 12 | POST | `/planning/plans/<plan_id>/runs/` | Explode (new run) | Step 3 |
| 13 | GET | `/planning/plans/<plan_id>/runs/` | List runs | — |
| 14 | GET | `/planning/plans/<plan_id>/runs/<run_id>/` | Run detail | — |
| 15 | GET | `/planning/plans/<plan_id>/runs/<run_id>/requirements/` | Requirement tree | Step 3 |
| 16 | POST | `/planning/requirements/<requirement_id>/allocations/` | Soft allocate | Step 4 |
| 17 | GET | `/planning/requirements/<requirement_id>/allocations/` | List soft allocs | — |
| 18 | DELETE | `/planning/allocations/<allocation_id>/` | Remove soft alloc (not committed) | — |
| 19 | POST | `/planning/plans/<plan_id>/commit/` | Harden allocs → stock reservations | Step 5 |
| 20 | GET | `/planning/plans/<plan_id>/events/` | Audit diary | — |
| 21 | GET | `/planning/supply/` | List projected supply | Optional |
| 22 | POST | `/planning/supply/` | Create supply row | Optional |
| 23 | PATCH | `/planning/supply/<id>/` | Update supply | — |
| 24 | DELETE | `/planning/supply/<id>/` | Delete supply | — |

**Out of MVP (Chunks 9–10):** resource board, demand profiles, shortage report endpoints.

---

## 2. Shared shapes

### Plan

```json
{
  "id": 1,
  "plan_date": "2026-08-04",
  "location_id": 10,
  "status": "draft",
  "remarks": "Monday samosa run",
  "created_by_user_id": 42,
  "created_at": "2026-08-03T08:55:00Z",
  "updated_at": "2026-08-03T08:55:00Z",
  "line_count": 1,
  "latest_run_id": 1,
  "latest_run_status": "complete"
}
```

### Plan line

```json
{
  "id": 1,
  "plan_id": 1,
  "product_id": 501,
  "quantity": "100.000000",
  "unit_id": 3,
  "source": "manual",
  "override_consider_stock": null,
  "override_full_batches": null,
  "override_align_last_batch": null,
  "recipe_version_id": 88,
  "sort_order": 0
}
```

### Plan run

```json
{
  "id": 1,
  "plan_id": 1,
  "run_number": 1,
  "status": "complete",
  "driver_version": "explode-1.1",
  "error_message": null,
  "started_at": "2026-08-03T09:00:00Z",
  "completed_at": "2026-08-03T09:00:02Z",
  "stamp_json": {
    "v": 1,
    "what": "explode",
    "actor_name": "Harvi",
    "at": "2026-08-03T09:00:00+00:00",
    "plan_id": 1,
    "run_id": 1,
    "run_number": 1,
    "driver": "explode-1.1",
    "line_count": 1
  }
}
```

### Requirement (flat; UI may tree by `parent_requirement_id`)

```json
{
  "id": 11,
  "run_id": 1,
  "plan_line_id": null,
  "parent_requirement_id": 10,
  "level": 2,
  "batch_number": 1,
  "position": null,
  "product_id": 502,
  "product_name": "Potato",
  "unit_name": "grams",
  "category_id": 1,
  "category_name": "Meals",
  "product_class_id": 1,
  "product_class_name": "Finished",
  "recipe_version_id": 90,
  "net_required": "40.000000",
  "gross_required": "42.000000",
  "yield_factor": "1.000000",
  "process_loss": "0.952381",
  "min_shelf_life_days": 3,
  "source_location_id": 10,
  "destination_location_id": 10,
  "default_resource_id": null,
  "stock_on_hand": "20.000000",
  "balance": "22.000000",
  "closed": false,
  "calc_json": {
    "v": 1,
    "kind": "child",
    "summary": "40 from parent 100 × BOM 12 / yield 1. Stock not applied.",
    "inputs": {
      "parent_gross": "100",
      "bom_qty": "12",
      "yield_factor": "1"
    },
    "steps": [
      {
        "op": "scale_bom",
        "formula": "parent_gross × bom_qty / yield",
        "from": "100 × 12 / 1",
        "to": "40"
      }
    ],
    "result": { "net": "40", "gross": "42" }
  }
}
```

### Allocation

```json
{
  "id": 1,
  "requirement_id": 13,
  "lot_id": 9001,
  "location_id": 20,
  "quantity": "15.000000",
  "stock_reservation_id": null
}
```

After commit, `stock_reservation_id` becomes e.g. `7001`.

### Supply

```json
{
  "id": 1,
  "product_id": 504,
  "location_id": 20,
  "expected_at": "2026-08-05T08:00:00Z",
  "quantity": "50.000000",
  "unit_id": 2,
  "kind": "purchase_order",
  "source_document_type": "po",
  "source_document_id": 1205,
  "remarks": null
}
```

---

## 3. Endpoints detail (Priya example)

### 3.1 Create plan — `POST /planning/plans/`

**Request**

```json
{
  "plan_date": "2026-08-04",
  "location_id": 10,
  "remarks": "Monday samosa run"
}
```

**Success `201`**

```json
{
  "status": "success",
  "message": "Plan created",
  "data": { "id": 1, "plan_date": "2026-08-04", "location_id": 10, "status": "draft", "remarks": "Monday samosa run", "created_by_user_id": 42, "created_at": "...", "updated_at": "...", "line_count": 0, "latest_run_id": null, "latest_run_status": null }
}
```

**Errors**

| Code | When |
|------|------|
| 400 | Missing date/location; invalid date |
| 409 | Plan already exists for that date + location |

---

### 3.2 List plans — `GET /planning/plans/`

**Query:** `plan_date_from`, `plan_date_to`, `location_id`, `status`, `limit` (default 50), `offset`

**Success `200`:** `data` = `{ "items": [ Plan, ... ], "count": N }`

---

### 3.3 Get / patch plan

- `GET /planning/plans/1/` → Plan + optional `lines: [...]` in `data`
- `PATCH /planning/plans/1/` body `{ "remarks": "..." }` only when `draft` (400 if locked/closed)

---

### 3.4 Lifecycle

| Call | From | To | Notes |
|------|------|-----|-------|
| `POST .../lock/` | draft | locked | Empty body |
| `POST .../close/` | draft or locked | closed | Releases open reservations for this plan |
| `POST .../reopen/` | closed | draft | Keeps historical runs |

Each writes a `plan_event`. Wrong status → `409`.

---

### 3.5 Lines — add 100 samosas

`POST /planning/plans/1/lines/`

```json
{
  "product_id": 501,
  "quantity": "100.000000",
  "unit_id": 3,
  "source": "manual",
  "recipe_version_id": 88
}
```

**Success `201`:** line object.

Only when plan `status=draft`. Locked/closed → `409`.

`PATCH` / `DELETE` same rule.

---

### 3.6 Explode — `POST /planning/plans/1/runs/`

**Request** (optional body)

```json
{ "async": false }
```

**Sync success `201`**

```json
{
  "status": "success",
  "message": "Plan run complete",
  "data": {
    "run": { "id": 1, "run_number": 1, "status": "complete", "...": "..." },
    "requirement_count": 4
  }
}
```

**Behaviour**

- Allowed when status `draft` or `locked`
- `SELECT FOR UPDATE` on plan; allocate next `run_number`
- Creates `plan_requirement` rows for that run only
- If engine throws: run `status=failed`, `error_message` set, HTTP `422` with run payload

**Async (later):** `{ "async": true }` → `202`, run `status=running`; poll `GET .../runs/<id>/`.

---

### 3.7 Requirements — `GET /planning/plans/1/runs/1/requirements/`

**Query:** `flat=true` (default) or `tree=true`

**Success:** `data.stamp` = run who/when/what (null on old runs). `data.items` = list of requirements (see shape above). Each item includes `product_name`, `unit_name`, and `calc_json` (null on old runs).

---

### 3.8 Soft allocate — `POST /planning/requirements/13/allocations/`

```json
{
  "lot_id": 9001,
  "location_id": 20,
  "quantity": "15.000000"
}
```

**Success `201`:** allocation with `stock_reservation_id: null`.

**Errors**

| Code | When |
|------|------|
| 400 | qty ≤ 0 |
| 409 | Plan closed; or requirement not on latest complete run |
| 422 | Lot fails FEFO / shelf-life rule; insufficient ATP |

---

### 3.9 Commit — `POST /planning/plans/1/commit/`

**Request** (optional)

```json
{
  "requirement_ids": null
}
```

`null` / omit = commit all soft allocations on latest complete run that have `stock_reservation_id` null.  
Or pass a list of requirement ids to limit scope.

**Success `200`**

```json
{
  "status": "success",
  "message": "Allocations committed",
  "data": {
    "committed_count": 1,
    "allocations": [
      { "id": 1, "requirement_id": 13, "lot_id": 9001, "location_id": 20, "quantity": "15.000000", "stock_reservation_id": 7001 }
    ]
  }
}
```

Creates `stock_reservation` with `source_document_type="plan"`, `source_document_id=<plan_id>`, `source_document_line=<requirement_id>`.

**Idempotency:** already-committed allocs skipped (not double-reserved). Partial failure → `422` with which ids failed; no silent partial commit preferred — use one DB transaction for the batch.

---

### 3.10 Events — `GET /planning/plans/1/events/`

`data.items` = `{ id, event_type, payload_json, actor_user_id, created_at }[]` newest first.

---

### 3.11 Supply CRUD

- `GET /planning/supply/?product_id=&location_id=&expected_at_from=&expected_at_to=`
- `POST /planning/supply/` — body matches Supply shape (without id)
- `PATCH` / `DELETE` by id

Used by explode netting when `expected_at <= need_by`.

---

## 4. Error envelope

```json
{
  "status": "error",
  "message": "Plan already exists for this date and location",
  "data": { "plan_date": "2026-08-04", "location_id": 10, "existing_plan_id": 1 }
}
```

| HTTP | Use |
|------|-----|
| 400 | Validation |
| 401 | Missing/invalid token |
| 404 | Unknown id |
| 409 | State conflict (wrong status, duplicate plan) |
| 422 | Business rule (shelf life, explode failure, ATP) |
| 500 | Unexpected |

---

## 5. Journey ↔ API (Priya, 100 samosas)

```
POST /planning/plans/                          → plan id=1 draft
POST /planning/plans/1/lines/                  → line 100×501
POST /planning/plans/1/runs/                   → run #1 + requirements
GET  /planning/plans/1/runs/1/requirements/    → tree/list
POST /planning/requirements/13/allocations/    → soft alloc lot 9001
POST /planning/plans/1/commit/                 → reservation 7001
POST /planning/plans/1/lock/                   → locked
POST /planning/plans/1/close/                  → closed (end of day)
```

Optional anytime: `POST /planning/supply/` for Tuesday flour.

---

## 6. Chunk 7 implementation notes

- Function views + `api_success`/`api_error` (same as stock/product)
- No DRF required
- Services from Chunk 6 behind these routes
- Do not expose deferred resource/forecast routes until Chunks 9–10

---

## 7. Sign-off

- [x] MVP routes cover create → explode → allocate → commit → lock/close  
- [x] Matches ERD entities  
- [x] Matches user journey example ids  
- [x] Envelope + auth aligned with existing backend  

**Chunk 3 complete.** Next: approve **Chunk 4** (frontend UI spec).
