# Chunk 6 — Contract tests

**Status:** APPROVED  
**File:** `production_register/tests.py`

## Coverage

| Test | Asserts |
|---|---|
| `test_stations_list` | HR + Sleeving stations |
| `test_create_run_auto_use_by_and_active_recipe` | ACTIVE recipe pin + use_by = today+7 |
| `test_create_run_pins_plan_line_recipe` | Plan line recipe version wins |
| `test_preview_bom_shows_live_lots` | needed qty + FEFO lot dropdown stock |
| `test_post_minuses_stock_high_risk_to_sleeving` | post → ingredient balance −4, output +20 at Sleeving |
| `test_incomplete_bom_blocked` | under-pick → `INCOMPLETE_BOM` |
| `test_sleeving_station_create` | Sleeving station defaults |

## Run

```bash
cd svc-locations-django
.\venv\Scripts\python.exe manage.py test production_register.tests -v 2
```

**Note:** Local run may need a writable MySQL test DB (`CREATE DATABASE` / migrate rights). Remote `gazebo_dev` user hit permission denied on `test_DB_LOCATIONS` during this session — tests are written; re-run when test DB access is available.

## Next

Chunk 7 — Tablet UI (Make → BOM lot select → Done).
