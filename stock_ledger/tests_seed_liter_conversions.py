"""Tests for seed_liter_conversions management command."""

from decimal import Decimal
from io import StringIO
from uuid import uuid4

from django.core.management import call_command
from django.test import TestCase

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductGoodsInType,
    ProductLabelMode,
    ProductPackaging,
    ProductSupplier,
    Range,
    Unit,
)
from stock_ledger.models import StockUnitConversion
from stock_ledger.util.conversions import packs_to_stock, seed_global_unit_conversions


class SeedLiterConversionsTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=91, name='Lit Class')
        Category.objects.create(id=91, name='Lit Cat')
        Range.objects.create(id=91, name='Lit Range')
        self.grams = Unit.objects.create(id=91, name='grams')
        self.liter = Unit.objects.create(id=92, name='Liter')
        self.each = Unit.objects.create(id=93, name='Each')
        Unit.objects.create(id=94, name='unit')
        Unit.objects.create(id=95, name='Box')
        Unit.objects.create(id=96, name='Kg')
        seed_global_unit_conversions()
        self.wh = Location.objects.create(id=91, name='Lit WH', visible=True)
        self.supplier = Location.objects.create(
            id=92, name='Lit Sup', visible=True,
        )
        self.cream = Product.objects.create(
            name='SINGLE CREAM TEST',
            recipe_code=f'CR{uuid4().hex[:6]}',
            product_class_id=91,
            category_id=91,
            range_id=91,
            unit=self.grams,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.OTHER,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.oil = Product.objects.create(
            name='OLIVE OIL TEST',
            recipe_code=f'OL{uuid4().hex[:6]}',
            product_class_id=91,
            category_id=91,
            range_id=91,
            unit=self.grams,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.OTHER,
            source_container=self.wh,
            destination_container=self.wh,
        )
        ProductSupplier.objects.create(
            product=self.cream,
            supplier=self.supplier,
            supplier_code='CREAM-1',
            supplier_product_name='Cream',
            outer_qty=Decimal('1'),
            outer_unit=self.each,
            inner_qty=Decimal('2.273'),
            inner_unit=self.liter,
            is_active=True,
        )
        ProductSupplier.objects.create(
            product=self.oil,
            supplier=self.supplier,
            supplier_code='OIL-1',
            supplier_product_name='Oil',
            outer_qty=Decimal('1'),
            outer_unit=self.each,
            inner_qty=Decimal('5'),
            inner_unit=self.liter,
            is_active=True,
        )

    def test_apply_seeds_cream_and_oil_densities(self):
        out = StringIO()
        call_command('seed_liter_conversions', '--apply', stdout=out)
        cream_pkg = ProductPackaging.objects.get(product_id=self.cream.id)
        oil_pkg = ProductPackaging.objects.get(product_id=self.oil.id)
        self.assertEqual(cream_pkg.unitary_weight, Decimal('1.000000'))
        self.assertEqual(oil_pkg.unitary_weight, Decimal('0.910000'))
        cream_conv = StockUnitConversion.objects.get(
            unit_id=self.liter.id, product_id=self.cream.id,
        )
        self.assertEqual(cream_conv.to_kg, Decimal('1.000000'))
        mapping = ProductSupplier.objects.get(product_id=self.cream.id)
        stock = packs_to_stock(Decimal('1'), mapping, self.cream)
        # 1 pack * 2.273 L * 1 kg/L / 0.001 kg/g = 2273 g
        self.assertEqual(stock, Decimal('2273.000000'))
