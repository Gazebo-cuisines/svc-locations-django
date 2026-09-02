"""One ledger unit (product.unit); packs/kg are display only."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductLabelMode,
    ProductSupplier,
    Range,
    Unit,
)
from stock_ledger.models import (
    StockBalance,
    StockEntry,
    StockLot,
    StockLotOrigin,
)
from stock_ledger.util import services
from stock_ledger.util.conversions import (
    StockValidationError,
    packs_to_stock,
    seed_global_unit_conversions,
    stock_to_kg,
    stock_to_packs,
)


class UomConversionTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=201, name='UoM Class')
        Category.objects.create(id=201, name='UoM Cat')
        Range.objects.create(id=201, name='UoM Range')
        self.grams = Unit.objects.create(id=201, name='grams')
        self.kg = Unit.objects.create(id=202, name='Kg')
        self.box = Unit.objects.create(id=203, name='Box')
        seed_global_unit_conversions()
        self.wh = Location.objects.create(id=201, name='UoM WH', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.wh, role=LocationRole.STORAGE,
        )
        self.supplier = Location.objects.create(id=202, name='UoM Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name='PEAS ( FROZEN )',
            recipe_code=f'VEGFRO-{uuid4().hex[:4]}',
            product_class_id=201,
            category_id=201,
            range_id=201,
            unit=self.grams,
            label_mode=ProductLabelMode.BATCH,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='GAZ-PEAS-10',
            supplier_product_name='PEAS 10KG',
            outer_qty=Decimal('1'),
            outer_unit=self.box,
            inner_qty=Decimal('10'),
            inner_unit=self.kg,
            is_default=True,
            is_active=True,
        )

    def test_five_boxes_of_10kg_are_50000_grams(self):
        stock = packs_to_stock(Decimal('5'), self.mapping, self.product)
        self.assertEqual(stock, Decimal('50000.000000'))
        self.assertEqual(stock_to_kg(stock, self.product), Decimal('50.000000'))
        self.assertEqual(
            stock_to_packs(stock, self.mapping, self.product),
            Decimal('5.000000'),
        )

    def test_receipt_stores_product_unit_not_inner_kg(self):
        lot = StockLot.objects.create(
            product=self.product,
            trace_number='T-PEAS-1',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        entry = services.receipt(
            idempotency_key=f'uom-peas-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            quantity=Decimal('5'),
            product_supplier=self.mapping,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        self.assertEqual(entry.quantity, Decimal('50000.000000'))
        self.assertEqual(entry.unit_id, self.grams.id)
        lot.refresh_from_db()
        self.assertEqual(lot.product_supplier_id, self.mapping.id)
        self.assertEqual(
            StockEntry.objects.get(pk=entry.pk).unit_id, self.grams.id,
        )

    def test_balances_and_remaining_show_supplier_packs(self):
        lot = StockLot.objects.create(
            product=self.product,
            trace_number='T-PEAS-PACK',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )
        services.receipt(
            idempotency_key=f'uom-pack-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            quantity=Decimal('2'),
            product_supplier=self.mapping,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        client = Client()
        balances = client.get(
            f'/stock/balances/?location_id={self.wh.id}'
            f'&product_id={self.product.id}',
        )
        self.assertEqual(balances.status_code, 200, balances.content)
        row = balances.json()['data'][0]
        self.assertEqual(Decimal(row['quantity']), Decimal('20000'))
        self.assertEqual(Decimal(row['pack_quantity']), Decimal('2'))
        self.assertEqual(row['pack_unit_name'], 'Box')
        self.assertEqual(Decimal(row['display_kg']), Decimal('20'))
        self.assertTrue(row['shape_format_label'])

        remaining = client.get(
            f'/stock/warehouse/remaining/?location_id={self.wh.id}',
        )
        self.assertEqual(remaining.status_code, 200, remaining.content)
        product = remaining.json()['data'][0]['products'][0]
        self.assertEqual(Decimal(product['remaining_qty']), Decimal('20000'))
        self.assertEqual(Decimal(product['pack_quantity']), Decimal('2'))
        self.assertEqual(product['pack_unit_name'], 'Box')
        self.assertEqual(Decimal(product['display_kg']), Decimal('20'))
        self.assertTrue(product['shape_format_label'])
        self.assertEqual(len(product['pack_breakdown']), 1)
        bd = product['pack_breakdown'][0]
        self.assertEqual(bd['lot_id'], lot.id)
        self.assertEqual(bd['trace_number'], 'T-PEAS-PACK')
        self.assertEqual(Decimal(bd['pack_quantity']), Decimal('2'))
        self.assertEqual(bd['pack_unit_name'], 'Box')
        self.assertEqual(bd['label'], f'2 Box ({bd["shape_format_label"]})')
        self.assertIsNone(product['loose_kg'])

    def test_remaining_breakdown_separates_shapes_and_loose(self):
        bag = Unit.objects.create(id=204, name='Bag')
        other = ProductSupplier.objects.create(
            product=self.product,
            supplier=self.supplier,
            supplier_code='GAZ-PEAS-20',
            supplier_product_name='PEAS 20KG',
            outer_qty=Decimal('1'),
            outer_unit=bag,
            inner_qty=Decimal('20'),
            inner_unit=self.kg,
            is_default=False,
            is_active=True,
        )
        lot_a = self._lot('SHAPE-A')
        lot_b = self._lot('SHAPE-B')
        lot_loose = self._lot('LOOSE')
        services.receipt(
            idempotency_key=f'uom-bd-a-{uuid4()}',
            lot=lot_a,
            location_id=self.wh.id,
            quantity=Decimal('2'),
            product_supplier=self.mapping,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        services.receipt(
            idempotency_key=f'uom-bd-b-{uuid4()}',
            lot=lot_b,
            location_id=self.wh.id,
            quantity=Decimal('1'),
            product_supplier=other,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        services.receipt(
            idempotency_key=f'uom-bd-loose-{uuid4()}',
            lot=lot_loose,
            location_id=self.wh.id,
            quantity=Decimal('5000'),
            unit_id=self.grams.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        client = Client()
        remaining = client.get(
            f'/stock/warehouse/remaining/?location_id={self.wh.id}',
        )
        self.assertEqual(remaining.status_code, 200, remaining.content)
        product = remaining.json()['data'][0]['products'][0]
        self.assertEqual(product['lot_count'], 3)
        self.assertEqual(len(product['pack_breakdown']), 2)
        labels = {row['label'] for row in product['pack_breakdown']}
        self.assertIn(
            f'2 Box ({self.mapping.shape_format_label})', labels,
        )
        self.assertIn(
            f'1 Bag ({other.shape_format_label})', labels,
        )
        self.assertEqual(Decimal(product['loose_kg']), Decimal('5'))
        lot_ids = [row['lot_id'] for row in product['pack_breakdown']]
        self.assertEqual(set(lot_ids), {lot_a.id, lot_b.id})

    def _lot(self, suffix: str) -> StockLot:
        return StockLot.objects.create(
            product=self.product,
            trace_number=f'T-{suffix}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 12, 1),
        )

    def test_receipt_converts_kg_into_product_grams(self):
        lot = self._lot('KG-IN')
        entry = services.receipt(
            idempotency_key=f'uom-kg-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.kg.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        self.assertEqual(entry.quantity, Decimal('10000.000000'))
        self.assertEqual(entry.unit_id, self.grams.id)
        bal = StockBalance.objects.get(lot=lot, location_id=self.wh.id)
        self.assertEqual(bal.quantity, Decimal('10000.000000'))

    def test_issue_grams_against_kg_receipt(self):
        lot = self._lot('MIX-ISSUE')
        services.receipt(
            idempotency_key=f'uom-mix-in-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.kg.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        issue = services.issue(
            idempotency_key=f'uom-mix-out-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            quantity=Decimal('500'),
            unit_id=self.grams.id,
        )
        self.assertEqual(issue.quantity, Decimal('-500.000000'))
        self.assertEqual(issue.unit_id, self.grams.id)
        bal = StockBalance.objects.get(lot=lot, location_id=self.wh.id)
        self.assertEqual(bal.quantity, Decimal('9500.000000'))

    def test_count_counted_kg_against_gram_on_hand(self):
        lot = self._lot('COUNT-KG')
        services.receipt(
            idempotency_key=f'uom-cnt-in-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            quantity=Decimal('10'),
            unit_id=self.kg.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
        )
        adj = services.count_adjustment(
            idempotency_key=f'uom-cnt-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            counted_quantity=Decimal('9'),
            unit_id=self.kg.id,
        )
        self.assertEqual(adj.quantity, Decimal('-1000.000000'))
        self.assertEqual(adj.unit_id, self.grams.id)
        bal = StockBalance.objects.get(lot=lot, location_id=self.wh.id)
        self.assertEqual(bal.quantity, Decimal('9000.000000'))

    def test_unknown_unit_conversion_fails(self):
        lot = self._lot('BAD-UNIT')
        with self.assertRaises(StockValidationError):
            services.receipt(
                idempotency_key=f'uom-bad-{uuid4()}',
                lot=lot,
                location_id=self.wh.id,
                quantity=Decimal('1'),
                unit_id=self.box.id,
                effective_at=timezone.now(),
                counterparty_location_id=self.supplier.id,
            )
