"""
Import legacy container tables from production DB into loc_* (DB_LOCATIONS).

Reads: tblcontainers, tblcontainerschildcontainers, tblcontainerspostaladdress,
       tblcontainerscontacts, tblcontainerscontactscontacts

Usage:
  python manage.py import_legacy_locations --dry-run
  python manage.py import_legacy_locations --flush
"""

import os

import MySQLdb
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from locations.legacy_import import (
    features_from_row,
    legacy_flag_true,
    legacy_real_stock,
    legacy_visible,
    roles_from_row,
)
from locations.models import (
    Location,
    LocationAddress,
    LocationContact,
    LocationEdge,
    LocationFeatureAssignment,
    LocationRelationType,
    LocationRoleAssignment,
    LocationStockProfile,
)


class Command(BaseCommand):
    help = 'ETL legacy tblcontainers* into loc_* (Chunk 3)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--legacy-db',
            default=os.getenv('LEGACY_DB_NAME', 'production'),
            help='Source database name (default: LEGACY_DB_NAME or production)',
        )
        parser.add_argument(
            '--flush',
            action='store_true',
            help='Delete all loc_* rows before import',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Read legacy and print counts only; no writes',
        )

    def handle(self, *args, **options):
        legacy_db = options['legacy_db']
        conn = self._legacy_connection(legacy_db)
        cur = conn.cursor(MySQLdb.cursors.DictCursor)

        containers = self._fetch_containers(cur)
        child_edges = self._fetch_child_edges(cur)
        postal = self._fetch_postal(cur)
        contacts = self._fetch_contacts(cur)
        contacts_typed = self._fetch_contacts_typed(cur)
        cur.close()
        conn.close()

        zone_edges = self._zone_edges_from_topcontainer(containers)
        self.stdout.write(
            f'Legacy {legacy_db}: locations={len(containers)} '
            f'zone_edges={len(zone_edges)} subordinate_edges={len(child_edges)} '
            f'postal={len(postal)} contacts={len(contacts)} typed_contacts={len(contacts_typed)}'
        )

        if options['dry_run']:
            return

        with transaction.atomic():
            if options['flush']:
                deleted, _ = Location.objects.all().delete()
                self.stdout.write(f'Flushed loc_* ({deleted} objects)')

            for row in containers:
                loc = Location.objects.create(
                    id=row['id'],
                    name=row['container'],
                    external_code=row.get('externalcode') or None,
                    visible=legacy_visible(row.get('containerVisible')),
                    static=legacy_flag_true(row.get('containerStatic')),
                    locked=legacy_flag_true(row.get('locked')),
                    remarks=row.get('remarks') or None,
                    po_remarks=row.get('poremarks') or None,
                )
                LocationRoleAssignment.objects.bulk_create(
                    [
                        LocationRoleAssignment(location=loc, role=role)
                        for role in roles_from_row(row)
                    ]
                )
                LocationFeatureAssignment.objects.bulk_create(
                    [
                        LocationFeatureAssignment(location=loc, feature=feature)
                        for feature in features_from_row(row)
                    ]
                )
                LocationStockProfile.objects.create(
                    location=loc,
                    stock_identifier=row['stockidentifier'],
                    production_identifier=row['productionidentifier'],
                    production_form_identifier=row.get('productionformidentifier') or None,
                    usage_form_identifier=row.get('usageformidentifier') or None,
                    real_stock=legacy_real_stock(row.get('realstock')),
                    min_shelf_life=row.get('minimumshelflife'),
                    max_shelf_life=row.get('maximumshelflife'),
                    use_by_modifier=row.get('usebymodifyer'),
                    extends_component_use_by=legacy_flag_true(row.get('extendsComponentUseBy')),
                    metric_unit=row.get('metricunit'),
                    document_header=row.get('documentheader') or None,
                    default_report=row.get('defaultreport') or None,
                )

                mainaddress = row.get('mainaddress')
                if mainaddress and str(mainaddress).strip():
                    LocationAddress.objects.create(
                        location=loc,
                        name=row['container'],
                        address=mainaddress,
                        is_primary=True,
                    )

            for parent_id, child_id in zone_edges:
                LocationEdge.objects.create(
                    parent_id=parent_id,
                    child_id=child_id,
                    relation_type=LocationRelationType.ZONE_GROUP,
                )

            for parent_id, child_id in child_edges:
                LocationEdge.objects.create(
                    parent_id=parent_id,
                    child_id=child_id,
                    relation_type=LocationRelationType.SUBORDINATE_STORAGE,
                )

            for row in postal:
                LocationAddress.objects.create(
                    location_id=row['container'],
                    name=row.get('name'),
                    contact_point_name=row.get('contactpointname'),
                    contact_point_phone=row.get('contactpointphone'),
                    address=row.get('address'),
                    is_primary=False,
                )

            for row in contacts:
                LocationContact.objects.create(
                    location_id=row['container'],
                    name=row.get('name'),
                    phone=row.get('namephone'),
                    email=row.get('nameemail'),
                )

            for row in contacts_typed:
                LocationContact.objects.create(
                    location_id=row['container'],
                    contact_type=row.get('contactstype'),
                    contact_value=row.get('contact'),
                    remarks=row.get('remarks'),
                )

        self._parity_check(len(containers), len(zone_edges), len(child_edges))
        self.stdout.write(self.style.SUCCESS('Import complete'))

    def _legacy_connection(self, database: str):
        host = os.getenv('DB_HOST')
        user = os.getenv('DB_USER')
        password = os.getenv('DB_PASSWORD')
        port = int(os.getenv('DB_PORT') or 3306)
        if not all([host, user, password]):
            raise CommandError('DB_HOST, DB_USER, DB_PASSWORD required in .env')
        try:
            return MySQLdb.connect(
                host=host,
                port=port,
                user=user,
                passwd=password,
                db=database,
                connect_timeout=30,
            )
        except MySQLdb.Error as exc:
            raise CommandError(f'Cannot connect to legacy DB {database}: {exc}') from exc

    def _fetch_containers(self, cur):
        cur.execute('SELECT * FROM tblcontainers ORDER BY id')
        return cur.fetchall()

    def _fetch_child_edges(self, cur):
        cur.execute(
            """
            SELECT parentContainer AS parent_id, childContainer AS child_id
            FROM tblcontainerschildcontainers
            WHERE parentContainer IS NOT NULL AND childContainer IS NOT NULL
            """
        )
        return [(r['parent_id'], r['child_id']) for r in cur.fetchall()]

    def _fetch_postal(self, cur):
        cur.execute('SELECT * FROM tblcontainerspostaladdress')
        return cur.fetchall()

    def _fetch_contacts(self, cur):
        cur.execute('SELECT * FROM tblcontainerscontacts')
        return cur.fetchall()

    def _fetch_contacts_typed(self, cur):
        cur.execute('SELECT * FROM tblcontainerscontactscontacts')
        return cur.fetchall()

    @staticmethod
    def _zone_edges_from_topcontainer(containers):
        edges = []
        for row in containers:
            top = row.get('topcontainer')
            cid = row['id']
            if top is not None and top != cid:
                edges.append((top, cid))
        return edges

    def _parity_check(self, legacy_count, zone_count, subordinate_count):
        loc_count = Location.objects.count()
        if loc_count != legacy_count:
            raise CommandError(f'Parity fail: loc_location={loc_count} legacy={legacy_count}')
        self.stdout.write(
            f'Parity OK: locations={loc_count} '
            f'zone_edges={LocationEdge.objects.filter(relation_type=LocationRelationType.ZONE_GROUP).count()} '
            f'(expected {zone_count}) '
            f'subordinate_edges={LocationEdge.objects.filter(relation_type=LocationRelationType.SUBORDINATE_STORAGE).count()} '
            f'(expected {subordinate_count})'
        )
