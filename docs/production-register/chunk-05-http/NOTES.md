# Chunk 5 — Views + HTTP

**Status:** APPROVED  
**Branch:** `production_register`  
**App:** `production_register/views.py` + `urls.py`  
**Mount:** `/production/`

## Endpoints wired

| Method | Path |
|--------|------|
| GET | `/production/stations/` |
| GET | `/production/stations/<code>/` |
| GET/POST | `/production/runs/` |
| GET/PATCH | `/production/runs/<id>/` |
| GET | `/production/runs/<id>/preview-consume/` |
| PUT | `/production/runs/<id>/consumptions/` |
| POST | `/production/runs/<id>/post/` |
| POST | `/production/runs/<id>/void/` |
| GET/POST | `/production/stations/<code>/downtime/` |
| DELETE | `/production/downtime/<id>/` |

## Behaviour notes

- Logic in `views.py` only (no `services.py`)
- Recipe pin: plan line version else latest ACTIVE
- BOM needed qty: `component.quantity * (made / process_loss) / batch_quantity` (batch_quantity default = 1 unit)
- Preview lots: `StockBalance` at consume location, FEFO sort
- Post: creates `StockLot` (origin=production) then `stock_ledger.production()`
- Void: draft status flip; posted → `stock_ledger.reversal` on output + consumptions

## Next

Chunk 6 — contract tests (HR + Sleeving + BOM + use-by).
