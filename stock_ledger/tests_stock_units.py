"""StockUnit layer: print / scan / consume / void / reprint."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import Category, Product, ProductClass, ProductSupplier, Range, Unit
from stock_ledger.models import (
    StockBalance,
    StockEntry,
    StockEntryType,
    StockLot,
    StockLotOrigin,
    StockPeriod,
    StockPeriodStatus,
    StockUnit,
    StockUnitPrintEvent,
    StockUnitPrintReason,
    StockUnitStatus,
)
from stock_ledger.util import services, stock_units
from stock_ledger.util.conversions import StockValidationError


class StockUnitTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=91, name='SU Class')
        Category.objects.create(id=91, name='SU Cat')
        Range.objects.create(id=91, name='SU Range')
        self.unit = Unit.objects.create(id=91, name='Kg')
        self.wh = Location.objects.create(id=91, name='SU Warehouse', visible=True)
        self.kitchen = Location.objects.create(id=92, name='SU Kitchen', visible=True)
        self.product = Product.objects.create(
            name=f'SU Potato {uuid4().hex[:8]}',
            recipe_code=f'SU{uuid4().hex[:6]}',
            product_class_id=91,
            category_id=91,
            range_id=91,
            unit=self.unit,
            external_barcode='12345678901234',
            source_container=self.wh,
            destination_container=self.kitchen,
        )
        StockPeriod.objects.get_or_create(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            defaults={'status': StockPeriodStatus.OPEN},
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 9, 1),
        )
        self.entry = services.receipt(
            idempotency_key=f'su-receipt-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('100'),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
        )

    def test_print_five_bags_and_overprint_guard(self):
        units = stock_units.create_units_for_entry(
            source_entry=self.entry,
            unit_count=5,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'print-{uuid4()}',
        )
        self.assertEqual(len(units), 5)
        self.assertEqual(units[0].quantity_remaining, Decimal('20'))
        bal = StockBalance.objects.get(lot=self.lot, location_id=self.wh.id)
        self.assertEqual(bal.quantity, Decimal('100'))

        gs1 = stock_units.build_gs1_payload(units[0])
        self.assertIn('(01)', gs1['payload_string'])
        self.assertIn('(21)', gs1['payload_string'])
        self.assertEqual(
            StockUnitPrintEvent.objects.filter(
                stock_unit=units[0],
                reason=StockUnitPrintReason.INITIAL,
            ).count(),
            1,
        )

        with self.assertRaises(StockValidationError):
            stock_units.create_units_for_entry(
                source_entry=self.entry,
                unit_count=1,
                quantity_per_unit=Decimal('20'),
                idempotency_key_prefix=f'print-over-{uuid4()}',
            )

    def test_partial_then_full_consume(self):
        unit = stock_units.create_units_for_entry(
            source_entry=self.entry,
            unit_count=1,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'print-c-{uuid4()}',
        )[0]

        r1 = stock_units.consume_unit(
            unit_serial=unit.unit_serial,
            entry_type=StockEntryType.ISSUE,
            quantity=Decimal('5'),
            idempotency_key=f'issue-{uuid4()}',
        )
        self.assertEqual(r1['unit'].quantity_remaining, Decimal('15'))
        self.assertEqual(r1['unit'].status, StockUnitStatus.PARTIALLY_CONSUMED)
        self.assertEqual(
            StockBalance.objects.get(lot=self.lot, location_id=self.wh.id).quantity,
            Decimal('95'),
        )

        r2 = stock_units.consume_unit(
            unit_serial=unit.unit_serial,
            entry_type=StockEntryType.ISSUE,
            quantity=Decimal('15'),
            idempotency_key=f'issue-{uuid4()}',
        )
        self.assertEqual(r2['unit'].quantity_remaining, Decimal('0'))
        self.assertEqual(r2['unit'].status, StockUnitStatus.CONSUMED)

    def test_void_and_reprint(self):
        unit = stock_units.create_units_for_entry(
            source_entry=self.entry,
            unit_count=1,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'print-v-{uuid4()}',
        )[0]
        reprinted = stock_units.reprint_unit(unit_serial=unit.unit_serial)
        self.assertEqual(reprinted['unit'].unit_serial, unit.unit_serial)
        self.assertTrue(
            StockUnitPrintEvent.objects.filter(
                stock_unit=unit,
                reason=StockUnitPrintReason.REPRINT,
            ).exists(),
        )

        voided = stock_units.void_unit(
            unit_serial=unit.unit_serial,
            reason='damaged label',
        )
        self.assertEqual(voided.status, StockUnitStatus.VOID)
        self.assertEqual(
            StockBalance.objects.get(lot=self.lot, location_id=self.wh.id).quantity,
            Decimal('100'),
        )
        with self.assertRaises(StockValidationError):
            stock_units.reprint_unit(unit_serial=unit.unit_serial)

    def test_receipt_api_optional_print(self):
        client = Client()
        supplier = Location.objects.create(id=94, name='SU Print Supplier', visible=True)
        LocationRoleAssignment.objects.create(
            location=supplier, role=LocationRole.SUPPLIER,
        )
        lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'TA{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 9, 1),
        )
        key = f'su-api-receipt-{uuid4()}'
        resp = client.post(
            '/stock/receipt/',
            data={
                'idempotency_key': key,
                'lot_id': lot.id,
                'location_id': self.wh.id,
                'quantity': '40',
                'unit_id': self.unit.id,
                'supplier_id': supplier.id,
                'print_unit_count': 2,
                'print_quantity_per_unit': '20',
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        data = body.get('data') or body
        self.assertIn('units', data)
        self.assertEqual(len(data['units']), 2)
        serial = data['units'][0]['unit_serial']

        lookup = client.get(f'/stock/stock-units/{serial}/')
        self.assertEqual(lookup.status_code, 200, lookup.content)
        detail = lookup.json().get('data') or lookup.json()
        self.assertEqual(detail['product']['id'], self.product.id)
        self.assertIn('gs1', detail)

    def test_receipt_api_product_supplier_sets_counterparty(self):
        supplier = Location.objects.create(id=93, name='SU Gazebo Supplier', visible=True)
        LocationRoleAssignment.objects.create(
            location=supplier, role=LocationRole.SUPPLIER,
        )
        mapping = ProductSupplier.objects.create(
            product=self.product,
            supplier=supplier,
            supplier_code='SU-POT-5X20',
            supplier_product_name='Potato 5x20kg',
            outer_qty=Decimal('5'),
            outer_unit=self.unit,
            inner_qty=Decimal('20'),
            inner_unit=self.unit,
            is_default=True,
            is_active=True,
        )
        lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'TB{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 9, 1),
        )
        client = Client()
        resp = client.post(
            '/stock/receipt/',
            data={
                'idempotency_key': f'su-api-receipt-sup-{uuid4()}',
                'lot_id': lot.id,
                'location_id': self.wh.id,
                'quantity': '100',
                'unit_id': self.unit.id,
                'product_supplier_id': mapping.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json().get('data') or resp.json()
        entry = StockEntry.objects.get(pk=data['id'])
        self.assertEqual(entry.counterparty_location_id, supplier.id)
        self.assertEqual(data.get('counterparty_location_id'), supplier.id)
        self.assertEqual(data.get('supplier_id'), supplier.id)
        self.assertEqual(data.get('supplier_name'), supplier.name)

    def test_purchase_receipt_requires_supplier(self):
        lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'TC{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=date(2026, 9, 1),
        )
        client = Client()
        resp = client.post(
            '/stock/receipt/',
            data={
                'idempotency_key': f'su-api-receipt-nosup-{uuid4()}',
                'lot_id': lot.id,
                'location_id': self.wh.id,
                'quantity': '10',
                'unit_id': self.unit.id,
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('supplier', (resp.json().get('message') or '').lower())

    def test_transfer_whole_bags_updates_unit_location(self):
        bags = stock_units.create_units_for_entry(
            source_entry=self.entry,
            unit_count=5,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'print-xfer-{uuid4()}',
        )
        moved = bags[:2]
        out, inn = services.transfer(
            idempotency_key=f'su-xfer-whole-{uuid4()}',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.kitchen.id,
            quantity=Decimal('40'),
            unit_id=self.unit.id,
            unit_moves=[
                {'unit_serial': u.unit_serial, 'quantity': '20'}
                for u in moved
            ],
        )
        self.assertEqual(out.entry_type, StockEntryType.TRANSFER_OUT)
        self.assertEqual(inn.entry_type, StockEntryType.TRANSFER_IN)
        self.assertEqual(
            StockBalance.objects.get(lot=self.lot, location_id=self.wh.id).quantity,
            Decimal('60'),
        )
        self.assertEqual(
            StockBalance.objects.get(
                lot=self.lot, location_id=self.kitchen.id,
            ).quantity,
            Decimal('40'),
        )
        for u in moved:
            u.refresh_from_db()
            self.assertEqual(u.location_id, self.kitchen.id)
            self.assertEqual(u.quantity_remaining, Decimal('20'))
            self.assertEqual(u.status, StockUnitStatus.ACTIVE)
        for u in bags[2:]:
            u.refresh_from_db()
            self.assertEqual(u.location_id, self.wh.id)

        with self.assertRaises(StockValidationError):
            services.transfer(
                idempotency_key=f'su-xfer-again-{uuid4()}',
                lot=self.lot,
                from_location_id=self.wh.id,
                to_location_id=self.kitchen.id,
                quantity=Decimal('20'),
                unit_id=self.unit.id,
                unit_moves=[
                    {'unit_serial': moved[0].unit_serial, 'quantity': '20'},
                ],
            )

    def test_transfer_partial_bag_drawdown_stays_at_source(self):
        bag = stock_units.create_units_for_entry(
            source_entry=self.entry,
            unit_count=1,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'print-partial-{uuid4()}',
        )[0]
        services.transfer(
            idempotency_key=f'su-xfer-partial-{uuid4()}',
            lot=self.lot,
            from_location_id=self.wh.id,
            to_location_id=self.kitchen.id,
            quantity=Decimal('8'),
            unit_id=self.unit.id,
            unit_moves=[
                {'unit_serial': bag.unit_serial, 'quantity': '8'},
            ],
        )
        bag.refresh_from_db()
        self.assertEqual(bag.location_id, self.wh.id)
        self.assertEqual(bag.quantity_remaining, Decimal('12'))
        self.assertEqual(bag.status, StockUnitStatus.PARTIALLY_CONSUMED)

    def test_transfer_api_unit_moves_response(self):
        bags = stock_units.create_units_for_entry(
            source_entry=self.entry,
            unit_count=2,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'print-api-{uuid4()}',
        )
        client = Client()
        resp = client.post(
            '/stock/transfer/',
            data={
                'idempotency_key': f'su-api-xfer-{uuid4()}',
                'lot_id': self.lot.id,
                'from_location_id': self.wh.id,
                'to_location_id': self.kitchen.id,
                'quantity': '40',
                'unit_id': self.unit.id,
                'unit_moves': [
                    {'unit_serial': bags[0].unit_serial, 'quantity': '20'},
                    {'unit_serial': bags[1].unit_serial, 'quantity': '20'},
                ],
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json().get('data') or resp.json()
        self.assertIn('out', data)
        self.assertIn('in', data)
        self.assertEqual(len(data.get('units') or []), 2)
        self.assertEqual(data['units'][0]['location_id'], self.kitchen.id)

        lookup = client.get(f'/stock/stock-units/{bags[0].unit_serial}/')
        detail = lookup.json().get('data') or lookup.json()
        self.assertEqual(detail['location']['id'], self.kitchen.id)
        self.assertEqual(detail['quantity_remaining'], '20.000000')
