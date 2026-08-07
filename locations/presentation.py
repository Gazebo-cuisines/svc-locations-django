from locations.models import Location
from locations.services.hierarchy import (
    get_subordinate_children,
    get_subordinate_parent,
    get_zone_parent,
)


def _iso_datetime(value):
    if value is None:
        return None
    return value.isoformat()


def location_list_dict(location: Location) -> dict:
    return {
        'container_id': location.id,
        'id': location.id,
        'name': location.name,
        'external_code': location.external_code,
        'visible': location.visible,
        'static': location.static,
        'locked': location.locked,
        'roles': list(location.roles.values_list('role', flat=True)),
        'features': list(location.features.values_list('feature', flat=True)),
    }


def location_detail_dict(location: Location) -> dict:
    data = location_list_dict(location)
    profile = getattr(location, 'stock_profile', None)
    data.update(
        {
            'remarks': location.remarks,
            'po_remarks': location.po_remarks,
            'created_at': _iso_datetime(location.created_at),
            'updated_at': _iso_datetime(location.updated_at),
            'zone_parent_id': get_zone_parent(location.id),
            'subordinate_parent_id': get_subordinate_parent(location.id),
            'subordinate_children': get_subordinate_children(location.id),
            'stock_profile': None
            if profile is None
            else {
                'stock_identifier': profile.stock_identifier,
                'production_identifier': profile.production_identifier,
                'production_form_identifier': profile.production_form_identifier,
                'usage_form_identifier': profile.usage_form_identifier,
                'real_stock': profile.real_stock,
                'show_incomplete_stock': profile.show_incomplete_stock,
                'min_shelf_life': profile.min_shelf_life,
                'max_shelf_life': profile.max_shelf_life,
                'use_by_modifier': profile.use_by_modifier,
                'extends_component_use_by': profile.extends_component_use_by,
                'metric_unit': profile.metric_unit,
                'document_header': profile.document_header,
                'default_report': profile.default_report,
            },
            'addresses': [
                {
                    'id': addr.id,
                    'name': addr.name,
                    'contact_point_name': addr.contact_point_name,
                    'contact_point_phone': addr.contact_point_phone,
                    'address': addr.address,
                    'is_primary': addr.is_primary,
                }
                for addr in location.addresses.all()
            ],
            'contacts': [
                {
                    'id': contact.id,
                    'name': contact.name,
                    'phone': contact.phone,
                    'email': contact.email,
                    'contact_type': contact.contact_type,
                    'contact_value': contact.contact_value,
                    'remarks': contact.remarks,
                }
                for contact in location.contacts.all()
            ],
        }
    )
    return data
