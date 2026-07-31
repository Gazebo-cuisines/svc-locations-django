# Chunk 5 — Django schema notes

**Branch:** `Plan`  
**App:** `planning/`  
**Migration:** `planning/migrations/0001_planning_schema_chunk5.py`

## Tables created

MVP: `plan`, `plan_line`, `plan_run`, `plan_requirement`, `plan_allocation`, `plan_supply`, `plan_event`  
Deferred (empty until later chunks): `resource`, `plan_resource_slot`, `demand_profile`

## Apply locally

```bash
cd svc-locations-django
git checkout Plan
python manage.py migrate planning
```

No HTTP handlers yet (Chunk 7). `planning/urls.py` is mounted at `/planning/` with empty urlpatterns.

No stored procedures or planning triggers.
