"""StockUnit layer: print / scan / consume / void / reprint."""

from datetime import date, timedelta
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
    StockEntryType,
    StockLot,
    StockLotOrigin,
    StockUnit,
    StockUnitPrintEvent,
    StockUnitPrintReason,
    StockUnitStatus,
)
from stock_ledger.util import entry_labels, entry_posting, services, stock_units
from stock_ledger.util.conversions import StockValidationError
from stock_ledger.util.scan import parse_gs1


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
            label_mode=ProductLabelMode.PER_UNIT,
            source_container=self.wh,
            destination_container=self.kitchen,
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
        # quantity body is pack count; stock in product.unit (here Kg = inner).
        self.assertEqual(entry.quantity, Decimal('10000.000000'))
        self.assertEqual(entry.unit_id, self.product.unit_id)
        self.assertEqual(Decimal(data.get('pack_quantity')), Decimal('100'))
        self.assertEqual(data.get('shape_multiplier'), '100')
        self.assertEqual(Decimal(data.get('quantity')), Decimal('10000'))
        lot.refresh_from_db()
        self.assertEqual(lot.product_supplier_id, mapping.id)

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
        self.assertEqual(Decimal(detail['quantity_remaining']), Decimal('20'))


class ProductBarcodeTests(TestCase):
    """One reusable product label, FIFO batch picking, label_mode guards."""

    def setUp(self):
        ProductClass.objects.create(id=93, name='BC Class')
        Category.objects.create(id=93, name='BC Cat')
        Range.objects.create(id=93, name='BC Range')
        self.unit = Unit.objects.create(id=93, name='Kg')
        self.wh = Location.objects.create(id=93, name='BC Unit 2', visible=True)
        self.kitchen = Location.objects.create(id=94, name='BC High Risk', visible=True)
        self.supplier = Location.objects.create(
            id=95, name='BC Chicken Supplier', visible=True,
        )
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.today = timezone.localdate()
        self.product = self._product(ProductLabelMode.PRODUCT)

    def _product(self, label_mode):
        return Product.objects.create(
            name=f'BC Chicken {uuid4().hex[:8]}',
            recipe_code=f'BC{uuid4().hex[:6]}',
            product_class_id=93,
            category_id=93,
            range_id=93,
            unit=self.unit,
            label_mode=label_mode,
            source_container=self.wh,
            destination_container=self.kitchen,
        )

    def _lot(self, *, product=None, use_by=None):
        return StockLot.objects.create(
            product=product or self.product,
            trace_number=f'BC{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            use_by=use_by,
        )

    def _receipt(self, lot, quantity, *, location=None, po_number='PO-BC-1'):
        return services.receipt(
            idempotency_key=f'bc-receipt-{uuid4()}',
            lot=lot,
            location_id=(location or self.wh).id,
            quantity=Decimal(quantity),
            unit_id=self.unit.id,
            effective_at=timezone.now(),
            counterparty_location_id=self.supplier.id,
            po_number=po_number,
        )

    def test_label_carries_product_id_only_and_batch_text(self):
        plain = self.client.get(f'/stock/products/{self.product.id}/label/')
        self.assertEqual(plain.status_code, 200, plain.content)
        data = plain.json()['data']
        self.assertEqual(data['bcid'], 'datamatrix')
        self.assertEqual(data['payload_string'], f'P{self.product.id}')
        self.assertIsNone(data['human_readable']['use_by'])
        self.assertIsNone(data['human_readable']['trace_number'])

        lot = self._lot(use_by=date(2026, 8, 20))
        with_lot = self.client.get(
            f'/stock/products/{self.product.id}/label/?lot_id={lot.id}',
        )
        self.assertEqual(with_lot.status_code, 200, with_lot.content)
        labelled = with_lot.json()['data']
        # Barcode is constant per product; only the printed text is batch specific.
        self.assertEqual(labelled['payload_string'], f'P{self.product.id}')
        text = labelled['human_readable']
        self.assertEqual(text['product_id'], self.product.id)
        self.assertEqual(text['product_name'], self.product.name)
        self.assertEqual(text['use_by'], '2026-08-20')
        self.assertEqual(text['trace_number'], lot.trace_number)
        self.assertEqual(text['unit_name'], 'Kg')

    def test_label_rejects_lot_from_another_product(self):
        other = self._product(ProductLabelMode.PRODUCT)
        foreign_lot = self._lot(product=other, use_by=date(2026, 8, 20))
        resp = self.client.get(
            f'/stock/products/{self.product.id}/label/?lot_id={foreign_lot.id}',
        )
        self.assertEqual(resp.status_code, 404, resp.content)

    def test_parse_gs1_reads_serial_from_legacy_label(self):
        ais = parse_gs1('(01)05012345678901(10)26218(17)260820(21)ABC123')
        self.assertEqual(ais['01'], '05012345678901')
        self.assertEqual(ais['10'], '26218')
        self.assertEqual(ais['17'], '260820')
        self.assertEqual(ais['21'], 'ABC123')
        self.assertEqual(parse_gs1(f'P{self.product.id}'), {})

    def test_scan_product_trace_preselects_lot(self):
        lot = self._lot(use_by=date(2026, 9, 1))
        lot.trace_number = '26218'
        lot.save(update_fields=['trace_number'])
        self._receipt(lot, '10')

        code = f'P{self.product.id}T26218'
        resp = self.client.get(f'/stock/scan/?code={code}')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['match_type'], 'product_trace')
        self.assertEqual(data['selected_lot_id'], lot.id)
        self.assertEqual(data['product']['product_id'], self.product.id)

        # Unknown product id → 404
        self.assertEqual(
            self.client.get('/stock/scan/?code=P99999999T26218').status_code,
            404,
        )
        # Known product, unknown trace → product only, no selected lot
        miss = self.client.get(
            f'/stock/scan/?code=P{self.product.id}TNOPE',
        ).json()['data']
        self.assertEqual(miss['match_type'], 'product')
        self.assertIsNone(miss['selected_lot_id'])

    def test_scan_accepts_product_code_bare_id_and_rejects_unknown(self):
        self._receipt(self._lot(use_by=date(2026, 9, 1)), '100')

        for code in (f'P{self.product.id}', str(self.product.id)):
            resp = self.client.get(f'/stock/scan/?code={code}')
            self.assertEqual(resp.status_code, 200, resp.content)
            data = resp.json()['data']
            self.assertEqual(data['match_type'], 'product')
            self.assertEqual(data['product']['product_id'], self.product.id)
            self.assertIsNone(data['selected_lot_id'])

        self.assertEqual(self.client.get('/stock/scan/?code=P99999999').status_code, 404)
        self.assertEqual(self.client.get('/stock/scan/?code=NOPE').status_code, 404)
        self.assertEqual(self.client.get('/stock/scan/?code=').status_code, 400)
        incomplete = self.client.get('/stock/scan/?code=E')
        self.assertEqual(incomplete.status_code, 400)
        self.assertIn('incomplete', incomplete.json()['message'].lower())

    def test_scan_wrong_product_returns_clear_409(self):
        other = self._product(ProductLabelMode.PRODUCT)
        other.name = 'SALT'
        other.save(update_fields=['name'])
        self.product.name = 'SUGAR'
        self.product.save(update_fields=['name'])

        resp = self.client.get(
            f'/stock/scan/?code=P{other.id}'
            f'&expected_product_id={self.product.id}',
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        body = resp.json()
        self.assertEqual(
            body['message'],
            'Please scan the barcode for SUGAR. The one you scanned is for SALT.',
        )
        self.assertEqual(body['data']['error'], 'wrong_product')
        self.assertEqual(body['data']['expected_product_id'], self.product.id)
        self.assertEqual(body['data']['scanned_product_id'], other.id)

        ok = self.client.get(
            f'/stock/scan/?code=P{self.product.id}'
            f'&expected_product_id={self.product.id}',
        )
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_scan_check_fifo_hides_batches_and_rejects_newer_lot(self):
        soon = self._lot(use_by=self.today + timedelta(days=3))
        later = self._lot(use_by=self.today + timedelta(days=30))
        soon_entry = self._receipt(soon, '10')
        later_entry = self._receipt(later, '20')
        loc = self.wh.id
        pid = self.product.id

        listed = self.client.get(
            f'/stock/scan/?code=P{pid}&location_id={loc}',
        )
        self.assertEqual(listed.json()['data']['batch_count'], 2)

        ok = self.client.get(
            f'/stock/scan/?code=E{soon_entry.id}&location_id={loc}'
            f'&expected_product_id={pid}&check_fifo=1',
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        data = ok.json()['data']
        self.assertEqual(data['batches'], [])
        self.assertEqual(data['batch_count'], 0)
        self.assertEqual(data['selected_lot_id'], soon.id)
        self.assertEqual(data['trace_number'], soon.trace_number)

        bad = self.client.get(
            f'/stock/scan/?code=E{later_entry.id}&location_id={loc}'
            f'&expected_product_id={pid}&check_fifo=1',
        )
        self.assertEqual(bad.status_code, 409, bad.content)
        body = bad.json()
        self.assertEqual(body['data']['error'], 'fifo_mismatch')
        self.assertEqual(body['data']['recommended_lot_id'], soon.id)
        self.assertEqual(body['data']['recommended_trace'], soon.trace_number)
        self.assertIn('older stock', body['message'].lower())

        self.assertEqual(
            self.client.get(
                f'/stock/scan/?code=E{soon_entry.id}&check_fifo=1',
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(
                f'/stock/scan/?code=P{pid}&location_id={loc}&check_fifo=1',
            ).status_code,
            400,
        )

    def test_scan_goods_out_newer_lot_returns_qty_and_fifo_warning(self):
        soon = self._lot(use_by=self.today + timedelta(days=3))
        later = self._lot(use_by=self.today + timedelta(days=30))
        self._receipt(soon, '10')
        later_entry = self._receipt(later, '20')
        loc = self.wh.id
        pid = self.product.id

        resp = self.client.get(
            f'/stock/scan/goods-out/?code=E{later_entry.id}&location_id={loc}'
            f'&expected_product_id={pid}',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['lot_id'], later.id)
        self.assertEqual(data['quantity'], '20')
        self.assertEqual(data['lot_quantity'], '20')
        self.assertEqual(data['entry_code'], f'E{later_entry.id}')
        self.assertFalse(data['fifo_ok'])
        self.assertEqual(data['recommended_lot_id'], soon.id)
        self.assertEqual(data['recommended_trace'], soon.trace_number)

        product_only = self.client.get(
            f'/stock/scan/goods-out/?code=P{pid}&location_id={loc}',
        )
        self.assertEqual(product_only.status_code, 400)

    def test_scan_goods_out_quantity_is_this_sticker_not_lot(self):
        lot = self._lot(use_by=self.today + timedelta(days=10))
        bag_a = self._receipt(lot, '20')
        self._receipt(lot, '20')

        resp = self.client.get(
            f'/stock/scan/goods-out/?code=E{bag_a.id}&location_id={self.wh.id}',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['quantity'], '20')
        self.assertEqual(data['lot_quantity'], '40')
        self.assertEqual(data['entry_code'], f'E{bag_a.id}')

    def _scan_out(self, entry, *, location=None):
        loc_id = (location or self.wh).id
        resp = self.client.get(
            f'/stock/scan/goods-out/?code=E{entry.id}&location_id={loc_id}',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()['data']

    def _transfer(self, lot, quantity, *, sticker=None, sender=None, **extra):
        body = {
            'idempotency_key': f'bc-draw-{uuid4()}',
            'lot_id': lot.id,
            'from_location_id': (sender or self.wh).id,
            'to_location_id': self.kitchen.id,
            'quantity': quantity,
            'unit_id': self.unit.id,
            **extra,
        }
        if sticker is not None:
            body['source_entry_code'] = f'E{sticker.id}'
        return self.client.post(
            '/stock/transfer/', data=body, content_type='application/json',
        )

    def test_sticker_draw_down_shows_on_rescan(self):
        lot = self._lot(use_by=self.today + timedelta(days=10))
        bag_a = self._receipt(lot, '20')
        bag_b = self._receipt(lot, '20')

        before = self._scan_out(bag_a)
        self.assertEqual(before['quantity'], '20')
        self.assertEqual(before['sticker_initial'], '20')
        self.assertEqual(before['lot_quantity'], '40')

        resp = self._transfer(lot, '5', sticker=bag_a)
        self.assertEqual(resp.status_code, 201, resp.content)
        moved = resp.json()['data']
        self.assertEqual(moved['out']['source_entry_id'], bag_a.id)
        self.assertEqual(moved['out']['source_entry_code'], f'E{bag_a.id}')
        # Inbound leg is new stock at the destination, not a draw on the label.
        self.assertIsNone(moved['in']['source_entry_id'])

        after = self._scan_out(bag_a)
        self.assertEqual(after['quantity'], '15')
        self.assertEqual(after['sticker_initial'], '20')
        self.assertEqual(after['lot_quantity'], '35')

        sibling = self._scan_out(bag_b)
        self.assertEqual(sibling['quantity'], '20')
        self.assertEqual(sibling['lot_quantity'], '35')

    def test_sticker_count_adjust_keeps_barcode_updates_qty(self):
        lot = self._lot(use_by=self.today + timedelta(days=10))
        bag = self._receipt(lot, '19')
        sibling = self._receipt(lot, '10')

        services.count_adjustment(
            idempotency_key=f'bc-cnt-{uuid4()}',
            lot=lot,
            location_id=self.wh.id,
            counted_quantity=Decimal('90'),
            unit_id=self.unit.id,
            source_entry=bag,
        )

        data = self._scan_out(bag)
        self.assertEqual(data['entry_code'], f'E{bag.id}')
        self.assertEqual(data['quantity'], '90')
        self.assertEqual(data['sticker_initial'], '19')
        other = self._scan_out(sibling)
        self.assertEqual(other['entry_code'], f'E{sibling.id}')
        self.assertEqual(other['quantity'], '10')

    def test_sticker_draw_rejects_over_pick_and_mismatch(self):
        lot = self._lot(use_by=self.today + timedelta(days=10))
        bag_a = self._receipt(lot, '20')
        other_lot = self._lot(use_by=self.today + timedelta(days=20))
        self._receipt(other_lot, '10')

        self.assertEqual(self._transfer(lot, '5', sticker=bag_a).status_code, 201)

        over = self._transfer(lot, '16', sticker=bag_a)
        self.assertEqual(over.status_code, 400, over.content)
        self.assertIn(f'E{bag_a.id}', over.json()['message'])
        self.assertIn('15', over.json()['message'])

        wrong_lot = self._transfer(other_lot, '5', sticker=bag_a)
        self.assertEqual(wrong_lot.status_code, 400, wrong_lot.content)
        self.assertIn(other_lot.trace_number, wrong_lot.json()['message'])

        wrong_place = self._transfer(lot, '5', sticker=bag_a, sender=self.kitchen)
        self.assertEqual(wrong_place.status_code, 400, wrong_place.content)
        self.assertIn(self.wh.name, wrong_place.json()['message'])

        # Rejects changed nothing: still 15 left on the label.
        self.assertEqual(self._scan_out(bag_a)['quantity'], '15')

    def test_queued_draw_counts_before_it_posts(self):
        lot = self._lot(use_by=self.today + timedelta(days=10))
        bag_a = self._receipt(lot, '20')

        resp = self._transfer(
            lot, '5', sticker=bag_a, queue_stock=True, lan_username='dave.k',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        out_id = resp.json()['data']['out']['id']

        queued = self._scan_out(bag_a)
        # Balance waits for the post, but the label is already committed.
        self.assertEqual(queued['lot_quantity'], '20')
        self.assertEqual(queued['quantity'], '15')
        self.assertEqual(queued['queued_quantity'], '5')

        self.assertEqual(len(queued['queued_draws']), 1)
        draw = queued['queued_draws'][0]
        self.assertEqual(draw['entry_id'], out_id)
        self.assertEqual(draw['entry_code'], f'E{out_id}')
        self.assertEqual(draw['quantity'], '5')
        self.assertEqual(draw['to_location_name'], self.kitchen.name)
        self.assertEqual(draw['queued_by'], 'dave.k')
        self.assertIsNotNone(draw['queued_at'])
        self.assertEqual(draw['label_status'], 'pending')
        self.assertEqual(draw['blocked_by'], 'label_not_printed')
        self.assertIn(f'E{out_id}', draw['next_step'])
        self.assertEqual(draw['post_endpoint'], f'/stock/entries/{out_id}/post/')

        # Once it posts, the balance catches up and nothing is pending.
        entry_labels.mark_printed(entry_id=out_id)
        entry_labels.verify_label(entry_id=out_id, code=f'E{out_id}')
        entry_posting.post_entry(entry_id=out_id)

        posted = self._scan_out(bag_a)
        self.assertEqual(posted['quantity'], '15')
        self.assertEqual(posted['lot_quantity'], '15')
        self.assertEqual(posted['queued_quantity'], '0')
        self.assertEqual(posted['queued_draws'], [])

    def test_unlinked_transfer_clamps_sticker_to_lot_balance(self):
        lot = self._lot(use_by=self.today + timedelta(days=10))
        bag_a = self._receipt(lot, '20')
        bag_b = self._receipt(lot, '20')

        resp = self._transfer(lot, '25')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIsNone(resp.json()['data']['out']['source_entry_id'])

        # 15 kg left on the lot, so neither label may promise its full 20.
        for bag in (bag_a, bag_b):
            data = self._scan_out(bag)
            self.assertEqual(data['quantity'], '15')
            self.assertEqual(data['sticker_initial'], '20')
            self.assertEqual(data['lot_quantity'], '15')

    def test_scan_returns_fifo_batches_with_supplier_and_days_left(self):
        soon = self._lot(use_by=self.today + timedelta(days=3))
        later = self._lot(use_by=self.today + timedelta(days=30))
        undated = self._lot(use_by=None)
        self._receipt(later, '20')
        self._receipt(undated, '30')
        self._receipt(soon, '10')

        resp = self.client.get(
            f'/stock/scan/?code=P{self.product.id}&location_id={self.wh.id}',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['batch_count'], 3)
        self.assertEqual(data['total_quantity'], '60.000000')

        # Oldest use_by first, undated last, regardless of receipt order.
        self.assertEqual(
            [row['lot_id'] for row in data['batches']],
            [soon.id, later.id, undated.id],
        )
        self.assertEqual([row['fifo_rank'] for row in data['batches']], [0, 1, 2])

        first = data['batches'][0]
        self.assertEqual(first['days_left'], 3)
        self.assertEqual(first['supplier_id'], self.supplier.id)
        self.assertEqual(first['supplier_name'], self.supplier.name)
        self.assertEqual(first['po_number'], 'PO-BC-1')
        self.assertEqual(first['trace_number'], soon.trace_number)
        self.assertEqual(first['origin'], StockLotOrigin.PURCHASE)
        self.assertIsNone(data['batches'][2]['days_left'])

    def test_scan_location_filter_narrows_to_one_department(self):
        here = self._lot(use_by=self.today + timedelta(days=5))
        there = self._lot(use_by=self.today + timedelta(days=6))
        self._receipt(here, '10')
        self._receipt(there, '10', location=self.kitchen)

        resp = self.client.get(
            f'/stock/scan/?code=P{self.product.id}&location_id={self.kitchen.id}',
        )
        data = resp.json()['data']
        self.assertEqual([row['lot_id'] for row in data['batches']], [there.id])

        both = self.client.get(f'/stock/scan/?code=P{self.product.id}').json()['data']
        self.assertEqual(both['batch_count'], 2)

    def test_scan_serial_preselects_its_batch(self):
        per_unit = self._product(ProductLabelMode.PER_UNIT)
        lot = self._lot(product=per_unit, use_by=date(2026, 9, 1))
        entry = self._receipt(lot, '40')
        bag = stock_units.create_units_for_entry(
            source_entry=entry,
            unit_count=2,
            quantity_per_unit=Decimal('20'),
            idempotency_key_prefix=f'bc-print-{uuid4()}',
        )[0]

        resp = self.client.get(f'/stock/scan/?code={bag.unit_serial}')
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['match_type'], 'unit_serial')
        self.assertEqual(data['selected_lot_id'], lot.id)
        self.assertEqual(data['product']['product_id'], per_unit.id)

    def test_balances_order_fifo(self):
        soon = self._lot(use_by=self.today + timedelta(days=2))
        later = self._lot(use_by=self.today + timedelta(days=40))
        self._receipt(later, '5')
        self._receipt(soon, '5')

        resp = self.client.get(
            f'/stock/balances/?product_id={self.product.id}&order=fifo',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(
            [row['lot_id'] for row in resp.json()['data']],
            [soon.id, later.id],
        )

        bad = self.client.get(f'/stock/balances/?product_id={self.product.id}&order=zzz')
        self.assertEqual(bad.status_code, 400)

    def test_product_mode_refuses_batch_labels(self):
        entry = self._receipt(self._lot(use_by=date(2026, 9, 1)), '500')
        with self.assertRaises(StockValidationError) as ctx:
            stock_units.create_units_for_entry(
                source_entry=entry,
                unit_count=50,
                quantity_per_unit=Decimal('10'),
                idempotency_key_prefix=f'bc-bad-{uuid4()}',
            )
        self.assertIn('reusable product label', str(ctx.exception))
        self.assertEqual(StockUnit.objects.filter(created_by_entry=entry).count(), 0)

    def test_batch_mode_prints_one_label_for_the_whole_pallet(self):
        batch_product = self._product(ProductLabelMode.BATCH)
        lot = self._lot(product=batch_product, use_by=date(2026, 9, 1))
        entry = self._receipt(lot, '500')

        with self.assertRaises(StockValidationError):
            stock_units.create_units_for_entry(
                source_entry=entry,
                unit_count=2,
                quantity_per_unit=Decimal('250'),
                idempotency_key_prefix=f'bc-batch-two-{uuid4()}',
            )
        with self.assertRaises(StockValidationError):
            stock_units.create_units_for_entry(
                source_entry=entry,
                unit_count=1,
                quantity_per_unit=Decimal('10'),
                idempotency_key_prefix=f'bc-batch-part-{uuid4()}',
            )

        units = stock_units.create_units_for_entry(
            source_entry=entry,
            unit_count=1,
            quantity_per_unit=Decimal('500'),
            idempotency_key_prefix=f'bc-batch-ok-{uuid4()}',
        )
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].quantity_initial, Decimal('500'))

    def test_receipt_api_rejects_bulk_stickers_for_product_mode(self):
        lot = self._lot(use_by=date(2026, 9, 1))
        resp = self.client.post(
            '/stock/receipt/',
            data={
                'idempotency_key': f'bc-api-receipt-{uuid4()}',
                'lot_id': lot.id,
                'location_id': self.wh.id,
                'quantity': '500',
                'unit_id': self.unit.id,
                'supplier_id': self.supplier.id,
                'print_unit_count': 50,
                'print_quantity_per_unit': '10',
            },
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn('reusable product label', resp.json()['message'])
