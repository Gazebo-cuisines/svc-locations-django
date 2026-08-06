from django.core.exceptions import ValidationError
from django.db import transaction

from locations.legacy_import import features_from_row, roles_from_row
from locations.models import (
    Location,
    LocationFeature,
    LocationFeatureAssignment,
    LocationRelationType,
    LocationRole,
    LocationRoleAssignment,
    LocationStockProfile,
)
from locations.services.hierarchy import remove_parent_edge, set_parent_edge

# Legacy product/stock reference checks (legacy_checks) deferred until
# product/stock tables are owned by new services.


def _set_roles(location: Location, roles: list[str]) -> None:
    invalid = [role for role in roles if role not in LocationRole.values]
    if invalid:
        raise ValidationError(f'Invalid roles: {", ".join(invalid)}')
    LocationRoleAssignment.objects.filter(location=location).delete()
    LocationRoleAssignment.objects.bulk_create(
        [LocationRoleAssignment(location=location, role=role) for role in roles]
    )


def _set_features(location: Location, features: list[str]) -> None:
    invalid = [feature for feature in features if feature not in LocationFeature.values]
    if invalid:
        raise ValidationError(f'Invalid features: {", ".join(invalid)}')
    LocationFeatureAssignment.objects.filter(location=location).delete()
    LocationFeatureAssignment.objects.bulk_create(
        [
            LocationFeatureAssignment(location=location, feature=feature)
            for feature in features
        ]
    )


def _sync_roles(location: Location, legacy_row: dict) -> None:
    _set_roles(location, roles_from_row(legacy_row))


def _sync_features(location: Location, legacy_row: dict) -> None:
    _set_features(location, features_from_row(legacy_row))


@transaction.atomic
def create_location(
    *,
    name: str,
    location_id: int | None = None,
    external_code: str | None = None,
    visible: bool = True,
    static: bool = False,
    locked: bool = False,
    remarks: str | None = None,
    po_remarks: str | None = None,
    roles: list[str] | None = None,
    features: list[str] | None = None,
    role_flags: dict | None = None,
    feature_flags: dict | None = None,
    stock_profile: dict | None = None,
    zone_parent_id: int | None = None,
    subordinate_parent_id: int | None = None,
) -> Location:
    if location_id is None:
        # ponytail: IntegerField PK (legacy); next free id. Race rare at our volume.
        last = (
            Location.objects.select_for_update()
            .order_by('-id')
            .values_list('id', flat=True)
            .first()
        )
        location_id = 1 if last is None else int(last) + 1
    elif Location.objects.filter(id=location_id).exists():
        raise ValidationError(f'Location id {location_id} already exists.')

    location = Location.objects.create(
        id=location_id,
        name=name,
        external_code=external_code or None,
        visible=visible,
        static=static,
        locked=locked,
        remarks=remarks,
        po_remarks=po_remarks,
    )

    if roles is not None:
        _set_roles(location, roles)
    elif role_flags is not None:
        _sync_roles(location, role_flags)
    if features is not None:
        _set_features(location, features)
    elif feature_flags is not None:
        _sync_features(location, feature_flags)

    profile = stock_profile or {}
    LocationStockProfile.objects.create(
        location=location,
        stock_identifier=profile.get('stock_identifier', 'STKUNDEF'),
        production_identifier=profile.get('production_identifier', 'PRODUNDEF'),
        production_form_identifier=profile.get('production_form_identifier'),
        usage_form_identifier=profile.get('usage_form_identifier'),
        real_stock=profile.get('real_stock', True),
        min_shelf_life=profile.get('min_shelf_life'),
        max_shelf_life=profile.get('max_shelf_life'),
        use_by_modifier=profile.get('use_by_modifier'),
        extends_component_use_by=profile.get('extends_component_use_by', False),
        metric_unit=profile.get('metric_unit'),
        document_header=profile.get('document_header'),
        default_report=profile.get('default_report'),
    )

    if zone_parent_id is not None:
        set_parent_edge(
            parent_id=zone_parent_id,
            child_id=location.id,
            relation_type=LocationRelationType.ZONE_GROUP,
        )
    if subordinate_parent_id is not None:
        set_parent_edge(
            parent_id=subordinate_parent_id,
            child_id=location.id,
            relation_type=LocationRelationType.SUBORDINATE_STORAGE,
        )

    return location


@transaction.atomic
def update_location(
    location_id: int,
    *,
    name: str | None = None,
    external_code: str | None = None,
    visible: bool | None = None,
    static: bool | None = None,
    locked: bool | None = None,
    remarks: str | None = None,
    po_remarks: str | None = None,
    roles: list[str] | None = None,
    features: list[str] | None = None,
    role_flags: dict | None = None,
    feature_flags: dict | None = None,
    stock_profile: dict | None = None,
    hierarchy: dict | None = None,
) -> Location:
    try:
        location = Location.objects.get(pk=location_id)
    except Location.DoesNotExist as exc:
        raise ValidationError(f'Location {location_id} not found.') from exc

    if name is not None:
        location.name = name
    if external_code is not None:
        location.external_code = external_code or None
    if visible is not None:
        location.visible = visible
    if static is not None:
        location.static = static
    if locked is not None:
        location.locked = locked
    if remarks is not None:
        location.remarks = remarks
    if po_remarks is not None:
        location.po_remarks = po_remarks
    location.save()

    if roles is not None:
        _set_roles(location, roles)
    elif role_flags is not None:
        _sync_roles(location, role_flags)
    if features is not None:
        _set_features(location, features)
    elif feature_flags is not None:
        _sync_features(location, feature_flags)

    if stock_profile is not None:
        LocationStockProfile.objects.update_or_create(
            location=location,
            defaults=stock_profile,
        )

    if hierarchy is not None:
        if 'zone_parent_id' in hierarchy:
            zone_parent_id = hierarchy['zone_parent_id']
            if zone_parent_id is None:
                remove_parent_edge(location.id, LocationRelationType.ZONE_GROUP)
            else:
                set_parent_edge(
                    parent_id=zone_parent_id,
                    child_id=location.id,
                    relation_type=LocationRelationType.ZONE_GROUP,
                )
        if 'subordinate_parent_id' in hierarchy:
            subordinate_parent_id = hierarchy['subordinate_parent_id']
            if subordinate_parent_id is None:
                remove_parent_edge(location.id, LocationRelationType.SUBORDINATE_STORAGE)
            else:
                set_parent_edge(
                    parent_id=subordinate_parent_id,
                    child_id=location.id,
                    relation_type=LocationRelationType.SUBORDINATE_STORAGE,
                )

    return location


@transaction.atomic
def delete_location(location_id: int) -> None:
    # TODO: assert_can_delete_location when product/stock services exist
    try:
        location = Location.objects.get(pk=location_id)
    except Location.DoesNotExist as exc:
        raise ValidationError(f'Location {location_id} not found.') from exc
    location.delete()
