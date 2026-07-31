# Chunk 10 — Forecast / shortage

**Status:** DONE (local; commit/push on `Plan` when ready)  
**Repos:** `svc-locations-django` + `gazeboo-cloud-web`  
**Schema:** `demand_profile` (from Chunk 5)

## Backend

- `planning/services/forecast.py` — upsert profiles; 5-day horizon; BOM-aware shortage; `age_open_plans` rollover
- Command: `python manage.py age_planning_plans [--as-of YYYY-MM-DD]`
- APIs: `/planning/demand-profiles/`, `/planning/forecast/horizon/`, `/shortage/`, `/age/`

## Frontend

- `/planning/forecast` — profiles / horizon / shortage tabs
- Nav: Demand forecast wired

## Explicit non-goals

No OLS trends SPs; confirmed sales orders as demand source can replace manual profiles later.
