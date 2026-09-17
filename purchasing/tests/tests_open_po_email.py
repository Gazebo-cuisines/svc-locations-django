"""Open PO reminder email: Ordered + Partial, newest PO date first."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.test import Client, TestCase

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductLabelMode,
    Range,
    Unit,
)
from purchasing.models import (
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from purchasing.services.open_po_email import (
    build_open_po_html,
    consolidated_po_rows,
    open_po_reminder_rows,
    po_page_url,
    send_open_po_reminder,
)
from stock_ledger.models import StockReportEmailRecipient
from stock_ledger.util.ses_mail import SesMailError


class OpenPoReminderEmailTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=411, name='PO Mail Class')
        Category.objects.create(id=411, name='PO Mail Cat')
        Range.objects.create(id=411, name='PO Mail Range')
        self.unit = Unit.objects.create(id=411, name='kg')
        self.wh = Location.objects.create(id=411, name='PO Mail WH', visible=True)
        self.supplier = Location.objects.create(
            id=412, name='PO Mail Sup', visible=True,
        )
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.product = Product.objects.create(
            name='Peas',
            recipe_code=f'PEA-{uuid4().hex[:4]}',
            product_class_id=411,
            category_id=411,
            range_id=411,
            unit=self.unit,
            label_mode=ProductLabelMode.BATCH,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.client = Client()

    def _po(self, *, number, status, ordered, qty=Decimal('10')):
        po = PurchaseOrder.objects.create(
            number=number,
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=status,
            ordered_at=ordered,
            expected_at=ordered,
            external_number=number,
        )
        PurchaseOrderLine.objects.create(
            purchase_order=po,
            line_no=1,
            product=self.product,
            unit=self.unit,
            qty_ordered=qty,
            qty_received=Decimal('0') if status != PurchaseOrderStatus.RECEIVED else qty,
            qty_balance=Decimal('0') if status == PurchaseOrderStatus.RECEIVED else qty,
        )
        return po

    def test_rows_newest_first_excludes_draft_and_received(self):
        self._po(
            number='14SEP',
            status=PurchaseOrderStatus.ORDERED,
            ordered=date(2026, 9, 14),
        )
        self._po(
            number='16SEP',
            status=PurchaseOrderStatus.PARTIAL,
            ordered=date(2026, 9, 16),
            qty=Decimal('50'),
        )
        self._po(
            number='15SEP',
            status=PurchaseOrderStatus.ORDERED,
            ordered=date(2026, 9, 15),
        )
        self._po(
            number='DRAFT',
            status=PurchaseOrderStatus.DRAFT,
            ordered=date(2026, 9, 17),
        )
        self._po(
            number='DONE',
            status=PurchaseOrderStatus.RECEIVED,
            ordered=date(2026, 9, 18),
        )
        numbers = [row['po_number'] for row in open_po_reminder_rows(date(2026, 9, 17))]
        self.assertEqual(numbers, ['16SEP', '15SEP', '14SEP'])

    def test_skip_when_no_recipients(self):
        self._po(
            number='16SEP',
            status=PurchaseOrderStatus.ORDERED,
            ordered=date(2026, 9, 16),
        )
        result = send_open_po_reminder(as_of=date(2026, 9, 17))
        self.assertTrue(result['skipped'])
        self.assertEqual(result['row_count'], 1)

    def test_send_uses_open_po_list_not_closing_stock(self):
        StockReportEmailRecipient.objects.create(
            email='stock@example.com',
            report_type=StockReportEmailRecipient.REPORT_CLOSING_STOCK,
        )
        StockReportEmailRecipient.objects.create(
            email='po@example.com',
            report_type=StockReportEmailRecipient.REPORT_OPEN_PO,
        )
        self._po(
            number='16SEP',
            status=PurchaseOrderStatus.ORDERED,
            ordered=date(2026, 9, 16),
        )
        with patch(
            'purchasing.services.open_po_email.send_email_with_attachment',
            return_value='msg-po',
        ) as send:
            result = send_open_po_reminder(as_of=date(2026, 9, 17))
        self.assertEqual(result['recipients'], ['po@example.com'])
        kwargs = send.call_args.kwargs
        self.assertIn('Please take action', kwargs['body_html'])
        self.assertIn('Not received yet', kwargs['body_html'])
        self.assertIn('Peas', kwargs['body_html'])
        self.assertIn('/purchasing/pos/', kwargs['body_html'])
        self.assertIn('beta.gazeboo.cloud', kwargs['body_html'])
        self.assertIn('cid:gazebo-logo', kwargs['body_html'])
        self.assertIn('Good Morning', kwargs['body_html'])

    def test_html_builder(self):
        po = self._po(
            number='12744',
            status=PurchaseOrderStatus.ORDERED,
            ordered=date(2026, 9, 16),
        )
        pos = consolidated_po_rows(open_po_reminder_rows(date(2026, 9, 17)))
        html_body = build_open_po_html(
            as_of=date(2026, 9, 17),
            pos=pos,
            unsubscribe_href='http://example/unsub',
        )
        self.assertIn('Gazeboo Cloud', html_body)
        self.assertIn('Good Morning', html_body)
        self.assertIn('Please take action', html_body)
        self.assertIn('Purchase Order report 17.09.2026', html_body)
        self.assertNotIn('Report date:', html_body)
        self.assertIn('Unsubscribe', html_body)
        self.assertIn(f'/purchasing/pos/{po.id}', html_body)
        self.assertEqual(po_page_url(po.id), f'https://beta.gazeboo.cloud/purchasing/pos/{po.id}')

    def test_same_product_on_one_po_is_consolidated(self):
        po = PurchaseOrder.objects.create(
            number='12744',
            supplier=self.supplier,
            ship_to_location=self.wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date(2026, 9, 16),
            expected_at=date(2026, 9, 16),
            external_number='12744',
        )
        for line_no, qty in ((1, Decimal('10')), (2, Decimal('5'))):
            PurchaseOrderLine.objects.create(
                purchase_order=po,
                line_no=line_no,
                product=self.product,
                unit=self.unit,
                qty_ordered=qty,
                qty_received=Decimal('0'),
                qty_balance=qty,
            )
        pos = consolidated_po_rows(open_po_reminder_rows(date(2026, 9, 17)))
        self.assertEqual(len(pos), 1)
        self.assertEqual(pos[0]['still_to_come'], 'Peas 15 kg')

    def test_future_delivery_is_excluded(self):
        self._po(
            number='12744',
            status=PurchaseOrderStatus.ORDERED,
            ordered=date(2026, 9, 16),
        )
        future = PurchaseOrder.objects.get(external_number='12744')
        future.expected_at = date(2026, 9, 24)
        future.save(update_fields=['expected_at'])
        self._po(
            number='DUE',
            status=PurchaseOrderStatus.ORDERED,
            ordered=date(2026, 9, 4),
        )
        numbers = [row['po_number'] for row in open_po_reminder_rows(date(2026, 9, 17))]
        self.assertEqual(numbers, ['DUE'])

    @patch('stock_ledger.views.reports.require_any_admin', return_value=None)
    @patch('stock_ledger.views.reports.attach_user', return_value=None)
    def test_recipient_lists_are_separate(self, *_mocks):
        r = self.client.post(
            '/stock/reports/email-recipients/',
            data='{"email": "both@example.com", "report_type": "open_po"}',
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 201)
        r = self.client.get(
            '/stock/reports/email-recipients/?report_type=open_po',
        )
        self.assertEqual(len(r.json()['data']), 1)
        r = self.client.get('/stock/reports/email-recipients/')
        self.assertEqual(r.json()['data'], [])

    def test_command_dry_run_and_ses_error(self):
        StockReportEmailRecipient.objects.create(
            email='po@example.com',
            report_type=StockReportEmailRecipient.REPORT_OPEN_PO,
        )
        call_command('email_open_po_reminder', as_of='2026-09-17', dry_run=True)
        from django.core.management.base import CommandError

        with patch(
            'purchasing.services.open_po_email.send_email_with_attachment',
            side_effect=SesMailError('SES_FROM_EMAIL is not configured.'),
        ):
            with self.assertRaises(CommandError):
                call_command('email_open_po_reminder', as_of='2026-09-17')
