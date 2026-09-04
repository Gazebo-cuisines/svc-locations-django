from django.http import HttpRequest

from locations.models import Location
from locations.services.location_tree import locations_queryset_to_tree
from locations.utils.api_response import api_error, api_success


def location_queryset():
    return Location.objects.prefetch_related('roles', 'features').order_by('name')


def apply_list_filters(request: HttpRequest, queryset):
    include_hidden = (request.GET.get('include_hidden') or '').lower() in (
        '1', 'true', 'yes',
    )
    if not include_hidden:
        queryset = queryset.filter(visible=True)
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
    storage_class = request.GET.get('storage_class')
    if storage_class:
        queryset = queryset.filter(name__icontains=storage_class)
    return queryset


def json_location_list(request: HttpRequest, queryset, serialize_list, *, message: str):
    queryset = apply_list_filters(request, queryset)
    results, count = locations_queryset_to_tree(queryset)
    return api_success(
        message,
        data={'count': count, 'results': results},
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
