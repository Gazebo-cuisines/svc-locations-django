# Chunk 7 — Tablet UI

**Status:** APPROVED  
**Repo:** `gazeboo-cloud-web`  
**Feature:** `src/features/production-register/`

## Flow

1. `/production` — station cards (High Risk / Sleeving)
2. `/production/:stationCode` — Make / Records
3. `/production/:stationCode/make` — product + qty → create draft run
4. `/production/:stationCode/runs/:runId/use` — BOM lot dropdown → confirm → post (stock minus)
5. `/production/:stationCode/records` — list / continue / void

Nav: **Production → Floor register**

## Files

- `api/productionClient.ts`
- `pages/{StationHome,StationActions,Make,UseBom,Records}Page.tsx`
- Wired in `App.tsx` + `data/nav.ts`

## Next

Chunk 8 — Internal Process + Warehouse + cutover.
