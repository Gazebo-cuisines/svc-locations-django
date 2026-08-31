"""Food/RM goods-in templates keyed by ProductTechnical.storage_regime."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductGoodsInType,
    ProductLabelMode,
    ProductStorageRegime,
    ProductSupplier,
    ProductTechnical,
    Range,
    Unit,
)
from purchasing.management.commands.seed_goods_in_templates import (
    seed_goods_in_templates,
)
from purchasing.models import PurchaseOrder, PurchaseOrderLine, PurchaseOrderStatus
from purchasing.services.goods_in_form import resolve_goods_in_form
from purchasing.services.header_qc import HeaderQcError, submit_header_qc


class GoodsInRegimeTemplateTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=71, name='Reg Class')
        Category.objects.create(id=71, name='Reg Cat')
        Range.objects.create(id=71, name='Reg Range')
        self.kg = Unit.objects.create(id=71, name='Kg')
        self.bag = Unit.objects.create(id=72, name='Bag')
        self.wh = Location.objects.create(id=71, name='Reg WH', visible=True)
        self.supplier = Location.objects.create(id=72, name='Reg Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )

    def _product(self, *, regime=None, gin=ProductGoodsInType.RAW_MATERIAL):
        product = Product.objects.create(
            name=f'RM {uuid4().hex[:8]}',
            recipe_code=f'RG{uuid4().hex[:6]}',
            product_class_id=71,
            category_id=71,
            range_id=71,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=gin,
            source_container=self.wh,
            destination_container=self.wh,
        )
        if regime is not None:
            ProductTechnical.objects.create(product=product, storage_regime=regime)
        ProductSupplier.objects.create(
            product=product,
            supplier=self.supplier,
            supplier_code=f'SC{product.id}',
            supplier_product_name=product.name,
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('1'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )
        return product

    def _po(self, *products):
        po = PurchaseOrder.objects.create(
            number=f'REG-{uuid4().hex[:6]}',
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date.today(),
        )
        for i, product in enumerate(products, start=1):
            mapping = product.suppliers.get(is_default=True)
            PurchaseOrderLine.objects.create(
                purchase_order=po,
                line_no=i,
                product=product,
                product_supplier=mapping,
                unit=self.kg,
                qty_ordered=Decimal('1'),
                qty_received=Decimal('0'),
                qty_balance=Decimal('1'),
                multiplier=mapping.multiplier,
                shape_format_label=mapping.shape_format_label,
                unit_cost=Decimal('1'),
            )
        return po

    def _codes(self, po, *, line_index=0):
        form = resolve_goods_in_form(po.id)
        header = {item['code'] for item in form['header']['items']}
        line = {item['code'] for item in form['lines'][line_index]['template']['items']}
        required = {
            item['code']
            for item in form['header']['items']
            if item['required']
        }
        return header, line, required, form

    def test_ambient_omits_temps(self):
        po = self._po(self._product(regime=ProductStorageRegime.AMBIENT))
        header, line, required, form = self._codes(po)
        self.assertNotIn('vehicle_temperature', header)
        self.assertNotIn('product_temperature', line)
        self.assertEqual(form['header']['storage_regime'], ProductStorageRegime.AMBIENT)

    def test_chilled_requires_vehicle_temp(self):
        po = self._po(self._product(regime=ProductStorageRegime.CHILLED))
        header, line, required, form = self._codes(po)
        self.assertIn('vehicle_temperature', header)
        self.assertIn('vehicle_temperature', required)
        self.assertIn('product_temperature', line)
        self.assertEqual(form['header']['storage_regime'], ProductStorageRegime.CHILLED)
        with self.assertRaises(HeaderQcError) as ctx:
            submit_header_qc(
                po.id,
                body={
                    'checked_by_user_id': 1,
                    'answers': {
                        'vehicle_clean_fb_pest_odour': {'value': True},
                        'primary_outer_packaging_damaged': {'value': False},
                        'reject_delivery': {'value': False},
                    },
                },
            )
        self.assertIn('vehicle_temperature', str(ctx.exception))

    def test_frozen_includes_temps(self):
        po = self._po(self._product(regime=ProductStorageRegime.FROZEN))
        header, line, _required, form = self._codes(po)
        self.assertIn('vehicle_temperature', header)
        self.assertIn('product_temperature', line)
        self.assertEqual(form['header']['storage_regime'], ProductStorageRegime.FROZEN)

    def test_mixed_po_uses_strictest_header(self):
        po = self._po(
            self._product(regime=ProductStorageRegime.AMBIENT),
            self._product(regime=ProductStorageRegime.FROZEN),
        )
        header, ambient_line, _required, form = self._codes(po, line_index=0)
        _h, frozen_line, _r, _f = self._codes(po, line_index=1)
        self.assertIn('vehicle_temperature', header)
        self.assertEqual(form['header']['storage_regime'], ProductStorageRegime.FROZEN)
        self.assertNotIn('product_temperature', ambient_line)
        self.assertIn('product_temperature', frozen_line)

    def test_no_technical_keeps_fallback_food_header(self):
        po = self._po(self._product())
        header, line, required, form = self._codes(po)
        self.assertIn('vehicle_temperature', header)
        self.assertNotIn('vehicle_temperature', required)
        self.assertIn('product_temperature', line)
        self.assertIsNone(form['header']['storage_regime'])
