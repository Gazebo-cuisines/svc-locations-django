# Chunk 9 — Resource board

**Status:** DONE (local; commit/push on `Plan` when ready)  
**Repos:** `svc-locations-django` + `gazeboo-cloud-web`  
**Schema:** `resource` + `plan_resource_slot` (already from Chunk 5)

## Backend

- `planning/services/schedule.py` — create/update resource; `sequence_plan`; reorder/assign/unschedule; forward-chain times via `ProductProduction` (avg minutes/rate + gap/dwell)
- APIs under `/planning/resources/` and `/planning/board/` (+ sequence, reorder, assign, unschedule)

## Frontend

- `/planning/board` — `ResourceBoardPage` (date columns, sequence by plan id, assign, up/down, remove)
- Nav: Resource board

## Explicit non-goal

Finite capacity / shifts — later epic.
