"""
Data migration: set unit_group + to_base_factor on known units.

Groups and base units:
  count  → base = each  (factor 1)
  weight → base = kg    (g = 0.001, kg = 1)
  liquid → base = L     (mL = 0.001, L = 1)

Unit names are matched case-insensitively so the seed survives casing differences.
Unknown units are left NULL — callers must handle NULL gracefully.
"""

from decimal import Decimal

from django.db import migrations


# (name_lower, group, to_base_factor)
UNIT_SEED = [
    # count group — base = each (factor 1)
    ('each',  'count', Decimal('1')),
    ('bag',   'count', Decimal('1')),
    ('box',   'count', Decimal('1')),
    ('of',    'count', Decimal('1')),
    ('pack',  'count', Decimal('1')),
    ('roll',  'count', Decimal('1')),
    ('unit',  'count', Decimal('1')),
    ('case',  'count', Decimal('1')),
    # weight group — base = kg
    ('kg',    'weight', Decimal('1')),
    ('kgs',   'weight', Decimal('1')),
    ('g',     'weight', Decimal('0.001')),
    ('grams', 'weight', Decimal('0.001')),
    ('gram',  'weight', Decimal('0.001')),
    # liquid group — base = L
    ('l',     'liquid', Decimal('1')),
    ('ltr',   'liquid', Decimal('1')),
    ('liter', 'liquid', Decimal('1')),
    ('litre', 'liquid', Decimal('1')),
    ('ml',    'liquid', Decimal('0.001')),
]


def seed_unit_groups(apps, schema_editor):
    Unit = apps.get_model('product', 'Unit')
    lookup = {name: (group, factor) for name, group, factor in UNIT_SEED}
    for unit in Unit.objects.all():
        key = unit.name.strip().lower()
        if key in lookup:
            unit.unit_group, unit.to_base_factor = lookup[key]
            unit.save(update_fields=['unit_group', 'to_base_factor'])


def unseed_unit_groups(apps, schema_editor):
    Unit = apps.get_model('product', 'Unit')
    Unit.objects.all().update(unit_group=None, to_base_factor=None)


class Migration(migrations.Migration):

    dependencies = [
        ('product', '0005_unit_group_and_factor'),
    ]

    operations = [
        migrations.RunPython(seed_unit_groups, reverse_code=unseed_unit_groups),
    ]
