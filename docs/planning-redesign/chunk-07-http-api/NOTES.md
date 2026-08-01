# Chunk 7 — Django HTTP APIs

**Branch:** `Plan`  
**Code:** `svc-locations-django/planning/views.py`, `planning/urls.py`  
**Mount:** `/planning/` (already in `core/urls.py`)

Implements all MVP routes from [PLANNING_API.md](../chunk-03-api/PLANNING_API.md). Matches Postman **Gazebo - Food API → Planning**.

## Picking list (derived)

`GET /planning/plans/<plan_id>/runs/<run_id>/picking-list/?from_location=`

- Aggregates open (`closed=false`) requirements by product + from/to + unit.
- Optional `from_location` = issuing department **name** (e.g. `Unit 11`).
- Response uses **names only** (`product`, `unit`, `from_location`, `to_location`) — no ids.
- `by_department[]`: `{ from_location, line_count }`.

## Apply

```bash
cd svc-locations-django
git checkout Plan
python manage.py migrate planning
python manage.py runserver
```

Then run Postman Planning folder against `{{locations_base_url}}`.
