# Chunk 7 — Django HTTP APIs

**Branch:** `Plan`  
**Code:** `svc-locations-django/planning/views.py`, `planning/urls.py`  
**Mount:** `/planning/` (already in `core/urls.py`)

Implements all MVP routes from [PLANNING_API.md](../chunk-03-api/PLANNING_API.md). Matches Postman **Gazebo - Food API → Planning**.

## Apply

```bash
cd svc-locations-django
git checkout Plan
python manage.py migrate planning
python manage.py runserver
```

Then run Postman Planning folder against `{{locations_base_url}}`.
