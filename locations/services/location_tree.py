from locations.models import Location, LocationEdge, LocationRelationType
from locations.presentation import location_list_dict


def _location_queryset():
    return Location.objects.prefetch_related('roles', 'features').order_by('name')


def expand_locations_with_zone_parents(
    locations_by_id: dict[int, Location],
) -> dict[int, Location]:
    while True:
        ids = set(locations_by_id.keys())
        missing_parent_ids = set(
            LocationEdge.objects.filter(
                relation_type=LocationRelationType.ZONE_GROUP,
                child_id__in=ids,
            )
            .exclude(parent_id__in=ids)
            .values_list('parent_id', flat=True)
        )
        if not missing_parent_ids:
            break
        for location in _location_queryset().filter(pk__in=missing_parent_ids):
            locations_by_id[location.id] = location
    return locations_by_id


def _sort_nodes_by_name(nodes: list[dict]) -> None:
    nodes.sort(key=lambda node: (node.get('name') or '').lower())
    for node in nodes:
        _sort_nodes_by_name(node['children'])


def build_location_tree(
    locations_by_id: dict[int, Location],
    zone_edges: list[tuple[int, int]] | None = None,
) -> list[dict]:
    if not locations_by_id:
        return []

    ids = set(locations_by_id.keys())
    nodes = {
        location_id: {**location_list_dict(location), 'children': []}
        for location_id, location in locations_by_id.items()
    }

    child_ids_with_parent_in_set: set[int] = set()
    if zone_edges is None:
        zone_edges = list(
            LocationEdge.objects.filter(
                relation_type=LocationRelationType.ZONE_GROUP,
                child_id__in=ids,
                parent_id__in=ids,
            ).values_list('parent_id', 'child_id')
        )

    for parent_id, child_id in zone_edges:
        if parent_id not in nodes or child_id not in nodes:
            continue
        nodes[parent_id]['children'].append(nodes[child_id])
        child_ids_with_parent_in_set.add(child_id)

    root_ids = ids - child_ids_with_parent_in_set
    roots = [nodes[root_id] for root_id in root_ids]
    _sort_nodes_by_name(roots)
    return roots


def locations_queryset_to_tree(queryset) -> tuple[list[dict], int]:
    locations_by_id = {location.id: location for location in queryset}
    locations_by_id = expand_locations_with_zone_parents(locations_by_id)
    tree = build_location_tree(locations_by_id)
    return tree, len(locations_by_id)
