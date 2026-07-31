"""
Upsert legacy production.tblcategories into product_category.

- Same id → update fields (no duplicate)
- Missing id → insert
- Extra local rows → left alone (not deleted)

Usage:
  python manage.py import_legacy_categories --dry-run
  python manage.py import_legacy_categories
"""

import os
from decimal import Decimal, InvalidOperation

import MySQLdb
from django.core.management.base import BaseCommand
from django.db import transaction

from product.models import Category, Unit


def _as_bool(value, default=False):
    if value is None:
        return default
    return bool(value)


def _as_bool_nullable(value):
    if value is None:
        return None
    return bool(value)


def _as_int(value, default=0):
    if value is None:
        return default
    return int(value)


def _as_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class Command(BaseCommand):
    help = 'Upsert tblcategories → product_category (update existing, insert missing)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--legacy-db',
            default=os.getenv('LEGACY_DB_NAME', 'production'),
            help='Source database name (default: LEGACY_DB_NAME or production)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Read legacy and report counts only; no writes',
        )

    def handle(self, *args, **options):
        legacy_db = options['legacy_db']
        rows = self._fetch_legacy(legacy_db)
        existing_ids = set(Category.objects.values_list('id', flat=True))
        unit_ids = set(Unit.objects.values_list('id', flat=True))

        to_create = [r for r in rows if r['id'] not in existing_ids]
        to_update = [r for r in rows if r['id'] in existing_ids]

        self.stdout.write(
            f'Legacy {legacy_db}.tblcategories={len(rows)} '
            f'local={len(existing_ids)} '
            f'would_update={len(to_update)} would_insert={len(to_create)}'
        )

        if options['dry_run']:
            return

        with transaction.atomic():
            # Pass 1: upsert scalar fields (parent deferred so FKs resolve).
            for row in rows:
                purchase_unit_id = row.get('purchaseunit')
                if purchase_unit_id not in unit_ids:
                    purchase_unit_id = None

                Category.objects.update_or_create(
                    id=row['id'],
                    defaults={
                        'name': (row.get('category') or '').strip() or f'category-{row["id"]}',
                        'is_default': _as_bool(row.get('catdefault'), False),
                        'is_container': _as_bool_nullable(row.get('container')),
                        'purchase_unit_id': purchase_unit_id,
                        'multiplier': _as_decimal(row.get('multiplier')),
                        'path': row.get('categorypath') or None,
                        'path_nodes': row.get('categorypathnodes') or None,
                        'code_generator': row.get('codegenerator') or None,
                        'code_generator_path': row.get('codegeneratorpath') or None,
                        'last_increment_auto_code': _as_int(
                            row.get('lastincrementautocode'), 0
                        ),
                        'item_flag': _as_int(row.get('itemflag'), -1),
                        'is_range': _as_bool(row.get('rangeflag'), False),
                        'is_resource': _as_bool(row.get('resourceflag'), False),
                        'is_container_flag': _as_bool(row.get('containerflag'), False),
                        'is_other': _as_bool(row.get('othersflag'), False),
                        'is_locked': _as_bool(row.get('locked'), False),
                        'is_locked_assigned': _as_bool(row.get('lockedAssigned'), False),
                        'is_locked_path': _as_bool(row.get('lockedPath'), False),
                        'remarks': row.get('remarks') or None,
                    },
                )

            # Pass 2: parent links (only when parent id exists locally).
            local_ids = set(Category.objects.values_list('id', flat=True))
            for row in rows:
                parent_id = row.get('parentid')
                if parent_id is not None and parent_id not in local_ids:
                    parent_id = None
                Category.objects.filter(pk=row['id']).update(parent_id=parent_id)

        self.stdout.write(
            self.style.SUCCESS(
                f'Done. product_category={Category.objects.count()} '
                f'updated={len(to_update)} inserted={len(to_create)}'
            )
        )

    def _fetch_legacy(self, legacy_db):
        conn = MySQLdb.connect(
            host=os.getenv('DB_HOST'),
            port=int(os.getenv('DB_PORT') or 3306),
            user=os.getenv('DB_USER'),
            passwd=os.getenv('DB_PASSWORD'),
            db=legacy_db,
            connect_timeout=15,
        )
        try:
            cur = conn.cursor(MySQLdb.cursors.DictCursor)
            cur.execute('SELECT * FROM tblcategories ORDER BY id')
            return list(cur.fetchall())
        finally:
            conn.close()
