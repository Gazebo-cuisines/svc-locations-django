from django.http import HttpRequest

from locations.models import Location
from locations.utils.api_response import api_error, api_success


def location_queryset():
    return Location.objects.prefetch_related(
        'roles',
        'features',
        'addresses',
        'contacts',
        'stock_profile',
    ).order_by('name')


def apply_list_filters(request: HttpRequest, queryset):
    role = request.GET.get('role')
    if role:
        queryset = queryset.filter(roles__role=role).distinct()
    zone_parent_id = request.GET.get('zone_parent_id')
    if zone_parent_id:
        queryset = queryset.filter(
            parent_edges__parent_id=zone_parent_id,
            parent_edges__relation_type='zone_group',
        ).distinct()
    subordinate_parent_id = request.GET.get('subordinate_parent_id')
    if subordinate_parent_id:
        queryset = queryset.filter(
            parent_edges__parent_id=subordinate_parent_id,
            parent_edges__relation_type='subordinate_storage',
        ).distinct()
    query = request.GET.get('q')
    if query:
        queryset = queryset.filter(name__icontains=query)
    return queryset


def json_location_list(request: HttpRequest, queryset, serialize_list, *, message: str):
    queryset = apply_list_filters(request, queryset)
    results = [serialize_list(item) for item in queryset]
    return api_success(
        message,
        data={'count': len(results), 'results': results},
    )


def json_location_detail(
    request: HttpRequest,
    location_id: int,
    queryset,
    serialize_detail,
    *,
    message: str,
    not_found_message: str = 'Location not found.',
):
    try:
        location = queryset.get(pk=location_id)
    except Location.DoesNotExist:
        return api_error(not_found_message, data=None, status_code=404)
    return api_success(message, data=serialize_detail(location))
