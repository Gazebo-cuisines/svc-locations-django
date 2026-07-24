"""
Seed a small mock graph into loc_* so relationships are easy to inspect.

Based on real production_dev samples (ids preserved):
  168 Cold Storage  -> parent
   23 Freezer - 4005012
   24 Freezer - 4005706
    3 Low Risk        (process area)
   13 EH Booths       (customer + address/contact)

Usage:
  python manage.py seed_mock_locations
  python manage.py seed_mock_locations --flush
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from locations.models import (
    Location,
    LocationAddress,
    LocationContact,
    LocationEdge,
    LocationFeature,
    LocationFeatureAssignment,
    LocationRole,
    LocationRoleAssignment,
    LocationStockProfile,
)


MOCK = [
    {
        'id': 168,
        'name': 'Cold Storage',
        'external_code': 'COLD',
        'visible': True,
        'static': False,
        'locked': True,
        'remarks': 'Mock: top-level cold store (legacy id 168)',
        'roles': [LocationRole.INTERNAL, LocationRole.STORAGE],
        'features': [LocationFeature.BREAKDOWN_LIST, LocationFeature.STAFF_BUDGET],
        'stock': {
            'stock_identifier': 'STKCOLD',
            'production_identifier': 'PRODCOLD',
            'real_stock': True,
        },
    },
    {
        'id': 23,
        'name': 'Freezer - 4005012',
        'external_code': 'FZ5012',
        'visible': True,
        'static': False,
        'locked': True,
        'remarks': 'Mock: child of Cold Storage',
        'roles': [LocationRole.INTERNAL, LocationRole.STORAGE],
        'features': [LocationFeature.BREAKDOWN_LIST],
        'stock': {
            'stock_identifier': 'STKFREEZER23',
            'production_identifier': 'PRODFREEZER23',
            'real_stock': True,
        },
        'parent_id': 168,
    },
    {
        'id': 24,
        'name': 'Freezer - 4005706',
        'external_code': 'FZ5706',
        'visible': True,
        'static': False,
        'locked': True,
        'remarks': 'Mock: sibling freezer under Cold Storage',
        'roles': [LocationRole.INTERNAL, LocationRole.STORAGE],
        'features': [LocationFeature.BREAKDOWN_LIST],
        'stock': {
            'stock_identifier': 'STKFREEZER24',
            'production_identifier': 'PRODFREEZER24',
            'real_stock': False,
        },
        'parent_id': 168,
    },
    {
        'id': 3,
        'name': 'Low Risk',
        'external_code': 'LOWRISK',
        'visible': True,
        'static': False,
        'locked': False,
        'remarks': 'Mock: process area (no parent edge)',
        'roles': [LocationRole.INTERNAL, LocationRole.PROCESS, LocationRole.TRANSFORM],
        'features': [
            LocationFeature.LINEAR_PLAN,
            LocationFeature.ISSUE_RECIPES,
            LocationFeature.STAFF_BUDGET,
        ],
        'stock': {
            'stock_identifier': 'STKLOWRISK',
            'production_identifier': 'PRODLOWRISK',
            'real_stock': True,
        },
    },
    {
        'id': 13,
        'name': 'EH Booths & CO LTD',
        'external_code': 'BOOTH',
        'visible': True,
        'static': False,
        'locked': False,
        'remarks': 'Mock: customer location with address + contact',
        'roles': [LocationRole.CUSTOMER],
        'features': [LocationFeature.CUSTOMER_ORDERS, LocationFeature.CUSTOMER_FORECASTS],
        'stock': {
            'stock_identifier': 'STKBOOTH',
            'production_identifier': 'PRODBOOTH',
            'real_stock': False,
        },
        'addresses': [
            {
                'name': 'PRESTON - MULTI PACK',
                'contact_point_name': 'E H BOOTH & CO LTD',
                'contact_point_phone': None,
                'address': (
                    'E. H. BOOTH & CO. LTD\n'
                    'FRESH FOOD DEPOT\n'
                    'DISTRIBUTION CENTER\n'
                    'BLUEBELL WAY, RIBBLETON\n'
                    'PRESTON, LANCS\n'
                    'PR2 5PY'
                ),
                'is_primary': True,
            },
        ],
        'contacts': [
            {
                'name': 'Booth Depot Desk',
                'phone': '01772-000000',
                'email': 'depot@example.booths',
                'contact_type': None,
                'contact_value': None,
                'remarks': 'Mock contact',
            },
        ],
    },
]

MOCK_IDS = [row['id'] for row in MOCK]


class Command(BaseCommand):
    help = 'Seed 5 mock locations into loc_* (based on legacy samples)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Delete existing mock ids before seeding',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options['flush']:
            deleted, _ = Location.objects.filter(id__in=MOCK_IDS).delete()
            self.stdout.write(f'Flushed mock locations ({deleted} objects)')

        for row in MOCK:
            loc, created = Location.objects.update_or_create(
                id=row['id'],
                defaults={
                    'name': row['name'],
                    'external_code': row['external_code'],
                    'visible': row['visible'],
                    'static': row['static'],
                    'locked': row['locked'],
                    'remarks': row.get('remarks'),
                },
            )
            self.stdout.write(f'{"Created" if created else "Updated"} Location {loc.id} {loc.name}')

            LocationRoleAssignment.objects.filter(location=loc).delete()
            LocationRoleAssignment.objects.bulk_create(
                [
                    LocationRoleAssignment(location=loc, role=role)
                    for role in row.get('roles', [])
                ]
            )

            LocationFeatureAssignment.objects.filter(location=loc).delete()
            LocationFeatureAssignment.objects.bulk_create(
                [
                    LocationFeatureAssignment(location=loc, feature=feature)
                    for feature in row.get('features', [])
                ]
            )

            stock = row.get('stock') or {}
            LocationStockProfile.objects.update_or_create(
                location=loc,
                defaults={
                    'stock_identifier': stock.get('stock_identifier', 'STKUNDEF'),
                    'production_identifier': stock.get('production_identifier', 'PROUNDEF'),
                    'real_stock': stock.get('real_stock', True),
                    'production_form_identifier': stock.get('production_form_identifier'),
                    'usage_form_identifier': stock.get('usage_form_identifier'),
                    'min_shelf_life': stock.get('min_shelf_life'),
                    'max_shelf_life': stock.get('max_shelf_life'),
                    'use_by_modifier': stock.get('use_by_modifier'),
                    'extends_component_use_by': stock.get('extends_component_use_by', False),
                    'metric_unit': stock.get('metric_unit'),
                    'document_header': stock.get('document_header'),
                    'default_report': stock.get('default_report'),
                },
            )

            LocationEdge.objects.filter(child=loc).delete()
            parent_id = row.get('parent_id')
            if parent_id is not None:
                LocationEdge.objects.create(parent_id=parent_id, child=loc)

            LocationAddress.objects.filter(location=loc).delete()
            for addr in row.get('addresses', []):
                LocationAddress.objects.create(location=loc, **addr)

            LocationContact.objects.filter(location=loc).delete()
            for contact in row.get('contacts', []):
                LocationContact.objects.create(location=loc, **contact)

        self.stdout.write(self.style.SUCCESS('Mock seed complete'))
        self.stdout.write(
            'Graph: 168 Cold Storage -> 23 Freezer, 24 Freezer | '
            '3 Low Risk (standalone) | 13 EH Booths (customer+addr+contact)'
        )
