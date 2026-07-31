# Chunk 8 — React Planning UI

**Status:** DONE (local; commit/push on `Plan` when approved)  
**Repo:** `gazeboo-cloud-web`  
**Spec:** [PLANNING_UI.md](../chunk-04-ui/PLANNING_UI.md)

## Delivered

- `src/features/planning/` — api client/mappers, hooks, pages
- Routes in `App.tsx`: `/planning` → plans, create, detail (+ requirements deep link), supply
- Nav: Production planning + Expected supply; Demand forecast stub
- Dashboard Planning tile → `/planning/plans`

## Manual check

1. Open `/planning/plans` against locations API on `Plan`
2. Create plan → add line → Run → Allocate → Commit → Lock
