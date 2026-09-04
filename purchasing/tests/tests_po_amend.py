"""Amend ordered / partial purchase orders."""

from decimal import Decimal
from uuid import uuid4

from django.test import Client, TestCase

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import Category, Product, ProductClass, Range, Unit
from purchasing.models import (
    PurchaseOrderDelivery,
    PurchaseOrderDeliveryStatus,
    PurchaseOrderHistory,
    PurchaseOrderHistoryEvent,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from purchasing.services.po import create_purchase_order


class PoAmendTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=71, name='Amend Class')
        Category.objects.create(id=71, name='Amend Cat')
        Range.objects.create(id=71, name='Amend Range')
        self.unit = Unit.objects.create(id=71, name='Kg')
        self.wh = Location.objects.create(id=71, name='Amend WH', visible=True)
        self.supplier = Location.objects.create(id=72, name='Amend Sup', visible=True)
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name=f'Amend Prod {uuid4().hex[:6]}',
            recipe_code=f'AM{uuid4().hex[:6]}',
            product_class_id=71,
            category_id=71,
            range_id=71,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.client = Client()
        self.po = create_purchase_order(
            supplier_id=self.supplier.id,
            ship_to_location_id=self.wh.id,
            lines=[{'product_id': self.product.id, 'qty_ordered': '5'}],
            sage_po_number=f'AMD-{uuid4().hex[:8].upper()}',
            status=PurchaseOrderStatus.ORDERED,
            require_sage_po_number=True,
        )
        self.line = self.po.lines.get()

    def test_amend_increases_qty_and_revision(self):
        resp = self.client.post(
            f'/purchasing/pos/{self.po.id}/amend/',
            data=(
                '{'
                f'"remarks":"plus two",'
                f'"lines":[{{"id":{self.line.id},"product_id":{self.product.id},'
                f'"qty_ordered":"7"}}]'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['revision_no'], 1)
        self.assertEqual(data['remarks'], 'plus two')
        self.assertEqual(data['lines'][0]['qty_ordered'], '7')
        self.assertEqual(data['lines'][0]['id'], self.line.id)
        hist = PurchaseOrderHistory.objects.get(
            purchase_order_id=self.po.id,
            event_type=PurchaseOrderHistoryEvent.AMEND,
        )
        before = hist.payload['before_json']
        after = hist.payload['after_json']
        self.assertEqual(before['lines'][0]['qty_ordered'], '5')
        self.assertEqual(after['lines'][0]['qty_ordered'], '7')
        self.assertEqual(before['Line 1 qty ordered'], '5')
        self.assertEqual(after['Line 1 qty ordered'], '7')
        self.assertEqual(before['Line 1 product'], self.product.name)

        timeline = self.client.get(f'/purchasing/pos/{self.po.id}/timeline/')
        self.assertEqual(timeline.status_code, 200)
        amend = next(
            e for e in timeline.json()['data'] if e['action'] == 'amend'
        )
        self.assertIn('Line 1 qty ordered', amend['changed_fields'])
        self.assertNotIn('lines', amend['changed_fields'])
        self.assertEqual(amend['before_json']['Line 1 qty ordered'], '5')
        self.assertEqual(amend['after_json']['Line 1 qty ordered'], '7')

    def test_amend_rejects_draft_and_qty_below_received(self):
        draft = create_purchase_order(
            supplier_id=self.supplier.id,
            lines=[{'product_id': self.product.id, 'qty_ordered': '1'}],
            sage_po_number=f'AMD-D-{uuid4().hex[:6].upper()}',
            status=PurchaseOrderStatus.DRAFT,
            require_sage_po_number=True,
        )
        bad_draft = self.client.post(
            f'/purchasing/pos/{draft.id}/amend/',
            data='{"remarks":"x"}',
            content_type='application/json',
        )
        self.assertEqual(bad_draft.status_code, 400)

        PurchaseOrderLine.objects.filter(pk=self.line.id).update(
            qty_received=Decimal('3'),
            qty_balance=Decimal('2'),
        )
        self.po.status = PurchaseOrderStatus.PARTIAL
        self.po.save(update_fields=['status'])

        too_low = self.client.post(
            f'/purchasing/pos/{self.po.id}/amend/',
            data=(
                '{'
                f'"lines":[{{"id":{self.line.id},"product_id":{self.product.id},'
                f'"qty_ordered":"2"}}]'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(too_low.status_code, 400)
        self.assertIn('received', too_low.json()['message'])

    def test_amend_received_adds_line_reopens_partial(self):
        PurchaseOrderLine.objects.filter(pk=self.line.id).update(
            qty_received=Decimal('5'),
            qty_balance=Decimal('0'),
            line_closed=True,
            stock_in_done=True,
        )
        self.po.status = PurchaseOrderStatus.RECEIVED
        self.po.save(update_fields=['status'])

        extra = Product.objects.create(
            name=f'Amend Extra {uuid4().hex[:6]}',
            recipe_code=f'AX{uuid4().hex[:6]}',
            product_class_id=71,
            category_id=71,
            range_id=71,
            unit=self.unit,
            source_container=self.wh,
            destination_container=self.wh,
        )
        resp = self.client.post(
            f'/purchasing/pos/{self.po.id}/amend/',
            data=(
                '{'
                f'"lines":['
                f'{{"id":{self.line.id},"product_id":{self.product.id},'
                f'"qty_ordered":"5"}},'
                f'{{"product_id":{extra.id},"qty_ordered":"2"}}'
                f']'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(data['revision_no'], 1)
        self.assertEqual(len(data['lines']), 2)
        kept = next(l for l in data['lines'] if l['id'] == self.line.id)
        self.assertEqual(kept['qty_ordered'], '5')
        self.assertEqual(kept['qty_received'], '5')
        new = next(l for l in data['lines'] if l['product_id'] == extra.id)
        self.assertEqual(new['qty_ordered'], '2')
        self.assertEqual(new['qty_received'], '0')

    def test_amend_received_with_booked_open_delivery(self):
        PurchaseOrderLine.objects.filter(pk=self.line.id).update(
            qty_ordered=Decimal('600'),
            qty_received=Decimal('600'),
            qty_balance=Decimal('0'),
            line_closed=True,
            stock_in_done=True,
        )
        self.po.status = PurchaseOrderStatus.RECEIVED
        self.po.save(update_fields=['status'])
        delivery = PurchaseOrderDelivery.objects.create(
            purchase_order=self.po,
            status=PurchaseOrderDeliveryStatus.OPEN,
        )
        delivery.lines.create(po_line=self.line, qty_received=Decimal('600'))

        resp = self.client.post(
            f'/purchasing/pos/{self.po.id}/amend/',
            data=(
                '{'
                f'"lines":[{{"id":{self.line.id},"product_id":{self.product.id},'
                f'"qty_ordered":"634"}}]'
                '}'
            ),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(data['lines'][0]['qty_ordered'], '634')
        self.assertEqual(data['lines'][0]['qty_received'], '600')
        self.assertEqual(data['lines'][0]['qty_balance'], '34')

    def test_cancel_empty_open_delivery_unblocks_amend(self):
        PurchaseOrderLine.objects.filter(pk=self.line.id).update(
            qty_ordered=Decimal('600'),
            qty_received=Decimal('600'),
            qty_balance=Decimal('0'),
            line_closed=True,
            stock_in_done=True,
        )
        self.po.status = PurchaseOrderStatus.RECEIVED
        self.po.save(update_fields=['status'])
        stale = PurchaseOrderDelivery.objects.create(
            purchase_order=self.po,
            status=PurchaseOrderDeliveryStatus.OPEN,
        )

        body = (
            '{'
            f'"lines":[{{"id":{self.line.id},"product_id":{self.product.id},'
            f'"qty_ordered":"634"}}]'
            '}'
        )
        blocked = self.client.post(
            f'/purchasing/pos/{self.po.id}/amend/',
            data=body,
            content_type='application/json',
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn(str(stale.id), blocked.json()['message'])

        no_reason = self.client.post(
            f'/purchasing/pos/{self.po.id}/deliveries/{stale.id}/cancel/',
            data='{}',
            content_type='application/json',
        )
        self.assertEqual(no_reason.status_code, 400)

        cancelled = self.client.post(
            f'/purchasing/pos/{self.po.id}/deliveries/{stale.id}/cancel/',
            data='{"reason":"empty visit, supplier amended to 634"}',
            content_type='application/json',
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        self.assertEqual(cancelled.json()['data']['status'], 'cancelled')

        again = self.client.post(
            f'/purchasing/pos/{self.po.id}/deliveries/{stale.id}/cancel/',
            data='{"reason":"twice"}',
            content_type='application/json',
        )
        self.assertEqual(again.status_code, 400)

        resp = self.client.post(
            f'/purchasing/pos/{self.po.id}/amend/',
            data=body,
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()['data']
        self.assertEqual(data['status'], 'partial')
        self.assertEqual(data['lines'][0]['qty_ordered'], '634')
        self.assertEqual(data['lines'][0]['qty_received'], '600')
        self.assertEqual(data['lines'][0]['qty_balance'], '34')

        timeline = self.client.get(f'/purchasing/pos/{self.po.id}/timeline/')
        actions = [e['action'] for e in timeline.json()['data']]
        self.assertIn('cancel', actions)

    def test_cannot_cancel_delivery_with_receipts(self):
        delivery = PurchaseOrderDelivery.objects.create(
            purchase_order=self.po,
            status=PurchaseOrderDeliveryStatus.OPEN,
        )
        delivery.lines.create(po_line=self.line, qty_received=Decimal('1'))
        resp = self.client.post(
            f'/purchasing/pos/{self.po.id}/deliveries/{delivery.id}/cancel/',
            data='{"reason":"nope"}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('goods booked in', resp.json()['message'])
