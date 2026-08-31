"""Partial goods-in QC draft (PATCH) does not complete header/line QC."""

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
    ProductSupplier,
    Range,
    Unit,
)
from purchasing.management.commands.seed_goods_in_templates import (
    seed_goods_in_templates,
)
from purchasing.models import PurchaseOrder, PurchaseOrderLine, PurchaseOrderStatus
from purchasing.services.adhoc_goods_in import (
    AdhocGoodsInError,
    start_adhoc_goods_in,
    submit_adhoc_header_qc,
)
from purchasing.services.delivery import create_delivery
from purchasing.services.draft_qc import (
    draft_adhoc_header_qc,
    draft_adhoc_line_qc,
    draft_header_qc,
)
from purchasing.services.header_qc import HeaderQcError, submit_header_qc


class DraftQcTests(TestCase):
    def setUp(self):
        seed_goods_in_templates()
        ProductClass.objects.create(id=41, name='Dr Class')
        Category.objects.create(id=41, name='Dr Cat')
        Range.objects.create(id=41, name='Dr Range')
        self.kg = Unit.objects.create(id=41, name='Kg')
        self.bag = Unit.objects.create(id=42, name='Bag')
        self.wh = Location.objects.create(id=41, name='Dr WH', visible=True)
        self.supplier = Location.objects.create(id=42, name='Dr Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.other = Product.objects.create(
            name=f'Clip {uuid4().hex[:8]}',
            recipe_code=f'CL{uuid4().hex[:6]}',
            product_class_id=41,
            category_id=41,
            range_id=41,
            unit=self.kg,
            label_mode=ProductLabelMode.PER_UNIT,
            goods_in_type=ProductGoodsInType.OTHER,
            source_container=self.wh,
            destination_container=self.wh,
        )
        mapping = ProductSupplier.objects.create(
            product=self.other,
            supplier=self.supplier,
            supplier_code='CLIP-1',
            supplier_product_name='Clip',
            outer_qty=Decimal('1'),
            outer_unit=self.bag,
            inner_qty=Decimal('1'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )
        self.po = PurchaseOrder.objects.create(
            number=f'DR-{uuid4().hex[:6]}',
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date.today(),
        )
        PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            line_no=1,
            product=self.other,
            product_supplier=mapping,
            unit=self.kg,
            qty_ordered=Decimal('1'),
            qty_received=Decimal('0'),
            qty_balance=Decimal('1'),
            multiplier=mapping.multiplier,
            shape_format_label=mapping.shape_format_label,
            unit_cost=Decimal('1'),
        )

    def test_adhoc_draft_then_submit(self):
        form = start_adhoc_goods_in(
            product_id=self.other.id,
            location_id=self.wh.id,
        )
        draft = draft_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 3,
                'answers': {'damaged_product': {'value': False}},
            },
        )
        self.assertTrue(draft['qc_draft'])
        self.assertIsNone(draft['checked_at'])
        self.assertFalse(
            draft['saved_header_answers']['damaged_product']['value'],
        )
        with self.assertRaises(AdhocGoodsInError) as ctx:
            submit_adhoc_header_qc(
                form['session_id'],
                body={
                    'checked_by_user_id': 3,
                    'answers': {'damaged_product': {'value': False}},
                },
            )
        self.assertIn('reject_delivery', str(ctx.exception))
        submitted = submit_adhoc_header_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 3,
                'answers': {
                    'damaged_product': {'value': False},
                    'reject_delivery': {'value': False},
                },
            },
        )
        self.assertFalse(submitted['qc_draft'])
        self.assertIsNotNone(submitted['checked_at'])
        with self.assertRaises(AdhocGoodsInError):
            draft_adhoc_header_qc(
                form['session_id'],
                body={
                    'checked_by_user_id': 3,
                    'answers': {'comment': {'value': 'late'}},
                },
            )

        line_draft = draft_adhoc_line_qc(
            form['session_id'],
            body={
                'checked_by_user_id': 3,
                'answers': {'spec_check': {'value': True}},
            },
        )
        self.assertTrue(line_draft['line']['qc_draft'])
        self.assertFalse(line_draft['line']['line_check_ok'])

    def test_po_header_draft(self):
        delivery = create_delivery(self.po.id)
        form = draft_header_qc(
            self.po.id,
            delivery_id=delivery.id,
            body={
                'checked_by_user_id': 3,
                'answers': {'damaged_product': {'value': False}},
            },
        )
        self.assertTrue(form['qc_draft'])
        self.assertIsNone(form['checked_at'])
        with self.assertRaises(HeaderQcError):
            submit_header_qc(
                self.po.id,
                delivery_id=delivery.id,
                body={
                    'checked_by_user_id': 3,
                    'answers': {'damaged_product': {'value': False}},
                },
            )
        submit_header_qc(
            self.po.id,
            delivery_id=delivery.id,
            body={
                'checked_by_user_id': 3,
                'answers': {
                    'damaged_product': {'value': False},
                    'reject_delivery': {'value': False},
                },
            },
        )
        with self.assertRaises(HeaderQcError):
            draft_header_qc(
                self.po.id,
                delivery_id=delivery.id,
                body={
                    'checked_by_user_id': 3,
                    'answers': {'comment': {'value': 'late'}},
                },
            )
