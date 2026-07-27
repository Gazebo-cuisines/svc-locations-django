from django.core.exceptions import ValidationError

from locations.models import LocationEdge, LocationRelationType


def get_zone_parent(location_id: int) -> int | None:
    edge = (
        LocationEdge.objects.filter(
            child_id=location_id,
            relation_type=LocationRelationType.ZONE_GROUP,
        )
        .values_list('parent_id', flat=True)
        .first()
    )
    return edge


def get_subordinate_parent(location_id: int) -> int | None:
    edge = (
        LocationEdge.objects.filter(
            child_id=location_id,
            relation_type=LocationRelationType.SUBORDINATE_STORAGE,
        )
        .values_list('parent_id', flat=True)
        .first()
    )
    return edge


def get_subordinate_children(parent_id: int) -> list[int]:
    return list(
        LocationEdge.objects.filter(
            parent_id=parent_id,
            relation_type=LocationRelationType.SUBORDINATE_STORAGE,
        ).values_list('child_id', flat=True)
    )


def _ancestors(location_id: int, relation_type: str) -> set[int]:
    seen: set[int] = set()
    stack = [location_id]
    while stack:
        current = stack.pop()
        parent_ids = LocationEdge.objects.filter(
            child_id=current,
            relation_type=relation_type,
        ).values_list('parent_id', flat=True)
        for parent_id in parent_ids:
            if parent_id in seen:
                continue
            seen.add(parent_id)
            stack.append(parent_id)
    return seen


def _descendants(location_id: int, relation_type: str) -> set[int]:
    seen: set[int] = set()
    stack = [location_id]
    while stack:
        current = stack.pop()
        child_ids = LocationEdge.objects.filter(
            parent_id=current,
            relation_type=relation_type,
        ).values_list('child_id', flat=True)
        for child_id in child_ids:
            if child_id in seen:
                continue
            seen.add(child_id)
            stack.append(child_id)
    return seen


def assert_valid_edge(parent_id: int, child_id: int, relation_type: str) -> None:
    if parent_id == child_id:
        raise ValidationError('A location cannot be its own parent.')

    if parent_id in _ancestors(child_id, relation_type):
        raise ValidationError('This edge would create a hierarchy cycle.')

    if child_id in _descendants(parent_id, relation_type):
        raise ValidationError('Child is already under this parent for this relation type.')


def set_parent_edge(
    *,
    parent_id: int,
    child_id: int,
    relation_type: str,
) -> LocationEdge:
    assert_valid_edge(parent_id, child_id, relation_type)
    LocationEdge.objects.filter(
        child_id=child_id,
        relation_type=relation_type,
    ).delete()
    return LocationEdge.objects.create(
        parent_id=parent_id,
        child_id=child_id,
        relation_type=relation_type,
    )


def remove_parent_edge(child_id: int, relation_type: str) -> None:
    LocationEdge.objects.filter(
        child_id=child_id,
        relation_type=relation_type,
    ).delete()
