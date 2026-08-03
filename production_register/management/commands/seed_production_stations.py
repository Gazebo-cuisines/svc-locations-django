"""
Seed floor production stations from existing locations.

Usage:
  python manage.py seed_production_stations
  python manage.py seed_production_stations --dry-run

Looks up locations by name (case-insensitive). Pass explicit IDs to override:

  python manage.py seed_production_stations \\
    --high-risk-id=4 --sleeving-id=5 --cooking-id=82 --cold-store-id=168
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from locations.models import Location
from production_register.models import ProductionStation

# (code, name, location_name_candidates, output_name_candidates, consume_same_as_location)
DEFAULTS = [
    (
        'high_risk',
        'High Risk',
        ['High Risk'],
        ['Sleeving'],
        True,
    ),
    (
        'sleeving',
        'Sleeving',
        ['Sleeving'],
        ['Sleeving', 'Dispatch'],
        True,
    ),
    (
        'cooking',
        'Cooking (Internal Process)',
        ['Cooking'],
        ['High Risk'],
        True,
    ),
    (
        'belts',
        'Belts (Internal Process)',
        ['Belts'],
        ['High Risk'],
        True,
    ),
    (
        'fryers',
        'Fryers (Internal Process)',
        ['Fryers'],
        ['High Risk'],
        True,
    ),
    (
        'warehouse',
        'Warehouse / Cold Storage',
        ['Cold Storage', 'Cold Store', 'Unit 11'],
        ['Cold Storage', 'Cold Store', 'Unit 11'],
        True,
    ),
]


def _find_location(names: list[str], override_id: int | None) -> Location | None:
    if override_id is not None:
        return Location.objects.filter(pk=override_id).first()
    for name in names:
        loc = Location.objects.filter(name__iexact=name).first()
        if loc is not None:
            return loc
    return None


class Command(BaseCommand):
    help = 'Seed prod_reg_station rows for floor register (HR, Sleeving, process, warehouse).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--high-risk-id', type=int, default=None)
        parser.add_argument('--sleeving-id', type=int, default=None)
        parser.add_argument('--cooking-id', type=int, default=None)
        parser.add_argument('--belts-id', type=int, default=None)
        parser.add_argument('--fryers-id', type=int, default=None)
        parser.add_argument('--cold-store-id', type=int, default=None)

    def handle(self, *args, **options):
        dry = options['dry_run']
        overrides = {
            'high_risk': options['high_risk_id'],
            'sleeving': options['sleeving_id'],
            'cooking': options['cooking_id'],
            'belts': options['belts_id'],
            'fryers': options['fryers_id'],
            'warehouse': options['cold_store_id'],
        }

        created = 0
        updated = 0
        skipped = 0

        with transaction.atomic():
            for code, name, loc_names, out_names, _ in DEFAULTS:
                loc = _find_location(loc_names, overrides.get(code))
                out = _find_location(out_names, None) or loc
                if loc is None:
                    self.stdout.write(self.style.WARNING(f'skip {code}: location not found'))
                    skipped += 1
                    continue
                if out is None:
                    out = loc

                defaults = {
                    'name': name,
                    'location_id': loc.id,
                    'default_output_location_id': out.id,
                    'default_consume_location_id': loc.id,
                    'is_active': True,
                }
                if dry:
                    self.stdout.write(f'would upsert {code} @ location={loc.id} output={out.id}')
                    created += 1
                    continue

                obj, was_created = ProductionStation.objects.update_or_create(
                    code=code,
                    defaults=defaults,
                )
                if was_created:
                    created += 1
                    self.stdout.write(self.style.SUCCESS(f'created {code} id={obj.id}'))
                else:
                    updated += 1
                    self.stdout.write(f'updated {code} id={obj.id}')

            if dry:
                transaction.set_rollback(True)

        self.stdout.write(
            f'done created={created} updated={updated} skipped={skipped} dry_run={dry}'
        )
