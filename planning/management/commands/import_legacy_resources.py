"""
Upsert legacy production.tblresources into resource + resource_group.

- Same id → update fields (no duplicate)
- Missing id → insert
- Extra local rows → left alone (not deleted)
- Missing container in loc_location → skip + warn

Usage:
  python manage.py import_legacy_resources --dry-run
  python manage.py import_legacy_resources
  python manage.py import_legacy_resources --legacy-host 192.168.16.241 --legacy-user root --legacy-password '...' --legacy-db production
"""

import os

import MySQLdb
from django.core.management.base import BaseCommand
from django.db import transaction

from locations.models import Location
from planning.models import Resource, ResourceGroup


class Command(BaseCommand):
    help = 'Upsert tblresources → resource / resource_group (update existing, insert missing)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--legacy-db',
            default=os.getenv('LEGACY_DB_NAME', 'production'),
            help='Source database name (default: LEGACY_DB_NAME or production)',
        )
        parser.add_argument(
            '--legacy-host',
            default=os.getenv('LEGACY_DB_HOST') or os.getenv('DB_HOST'),
            help='Legacy MySQL host (default: LEGACY_DB_HOST or DB_HOST)',
        )
        parser.add_argument(
            '--legacy-port',
            type=int,
            default=int(os.getenv('LEGACY_DB_PORT') or os.getenv('DB_PORT') or 3306),
            help='Legacy MySQL port',
        )
        parser.add_argument(
            '--legacy-user',
            default=os.getenv('LEGACY_DB_USER') or os.getenv('DB_USER'),
            help='Legacy MySQL user',
        )
        parser.add_argument(
            '--legacy-password',
            default=os.getenv('LEGACY_DB_PASSWORD') or os.getenv('DB_PASSWORD'),
            help='Legacy MySQL password',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Read legacy and report counts only; no writes',
        )

    def handle(self, *args, **options):
        legacy_db = options['legacy_db']
        rows = self._fetch_legacy(options)
        location_ids = set(Location.objects.values_list('id', flat=True))
        existing_ids = set(Resource.objects.values_list('id', flat=True))

        skipped = []
        usable = []
        for row in rows:
            container_id = row.get('container')
            if container_id not in location_ids:
                skipped.append(row['id'])
                continue
            usable.append(row)

        group_ids = {
            row['group'] for row in usable
            if row.get('group') is not None
        }
        to_create = [r for r in usable if r['id'] not in existing_ids]
        to_update = [r for r in usable if r['id'] in existing_ids]

        self.stdout.write(
            f'Legacy {options["legacy_host"]}/{legacy_db}.tblresources={len(rows)} '
            f'local={len(existing_ids)} '
            f'would_update={len(to_update)} would_insert={len(to_create)} '
            f'skip_missing_container={len(skipped)} groups={len(group_ids)}'
        )
        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    f'Skipped resource ids (container not in loc_location): {skipped[:20]}'
                    + ('...' if len(skipped) > 20 else '')
                )
            )

        if options['dry_run']:
            return

        with transaction.atomic():
            for gid in sorted(group_ids):
                ResourceGroup.objects.get_or_create(
                    id=gid,
                    defaults={'name': f'Group {gid}'},
                )

            used_codes: set[str] = set(
                Resource.objects.exclude(
                    id__in=[r['id'] for r in usable],
                ).values_list('code', flat=True)
            )

            for row in usable:
                code = self._unique_code(row['name'], row['id'], used_codes)
                used_codes.add(code)
                Resource.objects.update_or_create(
                    id=row['id'],
                    defaults={
                        'code': code,
                        'name': (row.get('name') or '').strip() or f'resource-{row["id"]}',
                        'location_id': row['container'],
                        'group_id': row.get('group'),
                        'is_active': True,
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. resource={Resource.objects.count()} '
                f'resource_group={ResourceGroup.objects.count()} '
                f'updated={len(to_update)} inserted={len(to_create)} '
                f'skipped={len(skipped)}'
            )
        )

    def _unique_code(self, name, resource_id, used_codes: set[str]) -> str:
        base = (name or '').strip() or f'resource-{resource_id}'
        base = base[:64]
        if base not in used_codes:
            return base
        suffix = f'-{resource_id}'
        return f'{base[: 64 - len(suffix)]}{suffix}'

    def _fetch_legacy(self, options):
        conn = MySQLdb.connect(
            host=options['legacy_host'],
            port=options['legacy_port'],
            user=options['legacy_user'],
            passwd=options['legacy_password'],
            db=options['legacy_db'],
            connect_timeout=15,
        )
        try:
            cur = conn.cursor(MySQLdb.cursors.DictCursor)
            cur.execute(
                'SELECT id, name, container, `group` AS `group` '
                'FROM tblresources ORDER BY id'
            )
            return list(cur.fetchall())
        finally:
            conn.close()
