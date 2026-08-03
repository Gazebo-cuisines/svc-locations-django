# Chunk 7 — Django HTTP APIs

**Branch:** `Plan`  
**Code:** `svc-locations-django/planning/views.py`, `planning/urls.py`  
**Mount:** `/planning/` (already in `core/urls.py`)

Implements all MVP routes from [PLANNING_API.md](../chunk-03-api/PLANNING_API.md). Matches Postman **Gazebo - Food API → Planning**.

## Picking list (derived)

`GET /planning/plans/<plan_id>/runs/<run_id>/picking-list/?from_location=&to_location=`

- Aggregates open (`closed=false`) requirements by product + from/to + unit.
- Optional `from_location` = issuing department **name** (outbound, e.g. `Unit 11`).
- Optional `to_location` = receiving department **name** (inbound, e.g. `Sleeving`).
- Each line: names + ids — `product`/`product_id`, `from_location`/`from_location_id`, `to_location`/`to_location_id`, `requirement_ids[]`.
- Top-level echoes `from_location` / `to_location` filters.
- `by_department[]`: `{ from_location, line_count }` (name-based).

## Plan publish

`POST /planning/plans/<plan_id>/publish/`

- Sets `published_at` (shop-floor visibility). Does not change `status`.
- Requires at least one **complete** run; rejects closed plans (409).
- Idempotent if already published (no second event).
- Writes `PlanEvent` `published`.
- Reopen clears `published_at` (must publish again).
- Plan payloads include `published_at`.

## Portal today

`GET /planning/portal/today/?location=&plan_date=&mode=`

- `location` (required): department **name** or **id** (e.g. `Unit 11` or `2`).
- `plan_date` (optional): `YYYY-MM-DD`, default today.
- `mode` (optional): `outbound` (default) or `inbound`.
- Returns published, non-closed plans for that date where the department appears on open requirements, each with latest complete run + scoped picking `lines` (with ids).
- Empty `items` if nothing published for that dept/day.

## Apply

```bash
cd svc-locations-django
git checkout Plan
python manage.py migrate planning
python manage.py runserver
```

Then run Postman Planning folder against `{{locations_base_url}}`.
