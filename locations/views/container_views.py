from collections import Counter

from django.views.decorators.http import require_GET

from locations.models import Location
from locations.utils.api_response import api_success

_CONTAINER_ENDPOINTS = {
    'containers': '/container/',
    'locations': '/container/locations/',
    'suppliers': '/container/suppliers/',
    'couriers': '/container/couriers/',
    'customers': '/container/customers/',
    'departments': '/container/departments/',
    'storage': '/container/storage/',
}

def container_list_dict(location: Location) -> dict:
    return {
        'id': location.id,
        'container_id': location.id,
        'name': location.name,
        'external_code': location.external_code,
        'visible': location.visible,
        'static': location.static,
        'locked': location.locked,
        'roles': list(location.roles.values_list('role', flat=True)),
    }

def global_container_list_dict(location: Location) -> dict:
    return {
        'container_id': location.id,
        'name': location.name,
        'external_code': location.external_code,
    }



@require_GET
def container_index_api(request):
    """List all containers (locations) for product FK selection and discovery."""
    qs = Location.objects.prefetch_related('roles').order_by('name')
    include_hidden = (request.GET.get('include_hidden') or '').lower() in (
        '1', 'true', 'yes',
    )
    visible = request.GET.get('visible')
    if visible is not None:
        qs = qs.filter(visible=visible.lower() in ('1', 'true', 'yes'))
    elif not include_hidden:
        qs = qs.filter(visible=True)
    query = request.GET.get('q')
    if query:
        qs = qs.filter(name__icontains=query)

    results = [container_list_dict(loc) for loc in qs]
    return api_success(
        'Container list fetched successfully.',
        data={
            'count': len(results),
            'results': results,
            'endpoints': _CONTAINER_ENDPOINTS,
        },
    )

@require_GET
def global_container_index_api(request):
    """List all containers (locations) for product FK selection and discovery."""
    qs = Location.objects.prefetch_related('roles').order_by('name').filter(visible=True)
    results = [global_container_list_dict(loc) for loc in qs]

    # ponytail: debug only — drop when done hunting dupes
    name_counts = Counter(r['name'] for r in results)
    for name, count in name_counts.items():
        if count > 1:
            print(f'DUPLICATE name={name!r} count={count}')

    return api_success(
        'Global container list fetched successfully.',
        data={
            'count': len(results),
            'results': results,
        },
    )