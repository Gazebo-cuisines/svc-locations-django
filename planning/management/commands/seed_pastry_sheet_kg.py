"""Seed pastry sheet → kg (Excel: kg = sheets × 10 / sheets-per-10kg-box).

PASTRY-01 LARGE  323 sheets / 10 kg
PASTRY-02 MEDIUM 667
PASTRY-08 SMALL  909

Writes packaging.unitary_weight (kg per sheet) and stock_unit_conversion
for unit name `unit`. Does not change ProductSupplier.multiplier.

  python manage.py seed_pastry_sheet_kg
  python manage.py seed_pastry_sheet_kg --dry-run
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from product.models import Product, ProductPackaging, Unit
from stock_ledger.models import StockUnitConversion
from stock_ledger.util.conversions import seed_global_unit_conversions

KG_PER_BOX = Decimal('10')
SHEETS_PER_BOX = {
    'PASTRY-01': 323,
    'PASTRY-02': 667,
    'PASTRY-08': 909,
}


class Command(BaseCommand):
    help = 'Set pastry kg-per-sheet from 10kg box sheet counts (323/667/909).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry = options['dry_run']
        sheet_unit = Unit.objects.filter(name='unit').first()
        if sheet_unit is None:
            self.stderr.write('Unit named "unit" not found.')
            return
        if not dry:
            seed_global_unit_conversions()
        written = 0
        missing = []
        for code, sheets in SHEETS_PER_BOX.items():
            product = Product.objects.filter(recipe_code=code).first()
            if product is None:
                missing.append(code)
                continue
            kg_per_sheet = (KG_PER_BOX / Decimal(sheets)).quantize(Decimal('0.000001'))
            self.stdout.write(f'{code}: {sheets} sheets/10kg -> {kg_per_sheet} kg/sheet')
            if dry:
                continue
            ProductPackaging.objects.update_or_create(
                product=product,
                defaults={'unitary_weight': kg_per_sheet},
            )
            StockUnitConversion.objects.update_or_create(
                unit=sheet_unit,
                product=product,
                defaults={'to_kg': kg_per_sheet, 'source': 'product_packaging'},
            )
            written += 1
        if missing:
            self.stdout.write('Not in catalogue: ' + ', '.join(missing))
        self.stdout.write(f'Updated {written} products.')
