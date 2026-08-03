# Chunk 8 — Internal Process + Warehouse + cutover

**Status:** APPROVED

## Delivered

### Backend (`svc-locations-django`)
- Relaxed `prod_reg_station.code` check so process cells can use codes like `cooking`, `belts`, `fryers` (migration `0002_relax_station_code_chunk8`)
- Management command:

```bash
python manage.py seed_production_stations
python manage.py seed_production_stations --high-risk-id=4 --sleeving-id=5 --cooking-id=82 --cold-store-id=168
```

Seeds: `high_risk`, `sleeving`, `cooking`, `belts`, `fryers`, `warehouse` when matching locations exist.

### Frontend (`gazeboo-cloud-web`)
- Station home blurb distinguishes warehouse vs process vs HR/Sleeving
- `warehouse` station → **Transfer stock** (`/stock/movements`) + stock overview (no MADE/CONSUME)
- Process stations (`cooking`, etc.) use same Make → Use → Records flow as HR/Sleeving

## Cutover checklist

1. Migrate: `python manage.py migrate production_register`
2. Seed stations against live locations
3. Parallel run: floor uses `/production` for HR + Sleeving; Access remains fallback
4. Freeze legacy SP changes for floor (no more `procSTKinternalProcessUSAGE` signature drift)
5. Retire Oct-2025 tablet wrappers (`gazebo_barcode` / `gazebo-highrisk-backend`) once stable
6. Add more process stations via seed or admin SQL as needed
7. Warehouse stays on `/stock/transfer` / movements — do not rebuild Access Stocks

## Out of this chunk
- Full cold-store bulk transfer UI (use existing stock movements)
- Auth hardening / offline
- Automatic plan-line prefill on Make (optional later)
