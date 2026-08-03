# Chunk 4 — Django schema notes

**Status:** APPROVED  
**Branch:** `production_register`  
**App:** `production_register/`  
**Migration:** `production_register/migrations/0001_prod_reg_schema_chunk4.py`

## Tables created

| Table | Model |
|---|---|
| `prod_reg_station` | `ProductionStation` |
| `prod_reg_run` | `ProductionRun` |
| `prod_reg_run_consumption` | `ProductionRunConsumption` |
| `prod_reg_downtime` | `ProductionDowntime` |

## Wired

- `INSTALLED_APPS` → `production_register`
- `path('production/', include('production_register.urls'))` — empty urlpatterns until Chunk 5

## Apply locally

```bash
cd svc-locations-django
python manage.py migrate production_register
```

No HTTP handlers yet (Chunk 5). No `services.py`. No stored procedures.
