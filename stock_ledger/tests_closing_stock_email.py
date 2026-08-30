"""Closing-stock daily email: recipients API + SES command helpers."""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import (
    Category,
    Product,
    ProductClass,
    ProductGoodsInType,
    Range,
    Unit,
)
from stock_ledger.models import StockReportEmailRecipient, StockLot, StockLotOrigin
from stock_ledger.util import services
from stock_ledger.util.closing_stock_email import (
    build_closing_stock_html,
    rows_to_csv,
    send_closing_stock_report,
    unsubscribe_token,
)
from stock_ledger.util.ses_mail import SesMailError


class ClosingStockEmailTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=91, name='Mail Class')
        Category.objects.create(id=91, name='Mail Cat')
        Range.objects.create(id=91, name='Mail Range')
        self.unit = Unit.objects.create(id=91, name='Kg')
        self.wh = Location.objects.create(id=91, name='Mail WH', visible=True)
        self.product = Product.objects.create(
            name=f'Mail Flour {uuid4().hex[:8]}',
            recipe_code=f'MF{uuid4().hex[:6]}',
            product_class_id=91,
            category_id=91,
            range_id=91,
            unit=self.unit,
            goods_in_type=ProductGoodsInType.RAW_MATERIAL,
            source_container=self.wh,
            destination_container=self.wh,
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 9, 1),
        )
        tz = timezone.get_current_timezone()
        services.receipt(
            idempotency_key=f'mail-in-{uuid4()}',
            lot=self.lot,
            location_id=self.wh.id,
            quantity=Decimal('40'),
            unit_id=self.unit.id,
            effective_at=timezone.make_aware(datetime(2026, 8, 10, 10, 0, 0), tz),
        )
        self.client = Client()

    @patch('stock_ledger.views.require_any_admin', return_value=None)
    @patch('stock_ledger.views.attach_user', return_value=None)
    def test_recipient_crud(self, *_mocks):
        r = self.client.post(
            '/stock/reports/email-recipients/',
            data='{"email": "ops@example.com"}',
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 201)
        pk = r.json()['data']['id']

        r = self.client.get('/stock/reports/email-recipients/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['data']), 1)

        r = self.client.patch(
            f'/stock/reports/email-recipients/{pk}/',
            data='{"is_active": false}',
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['data']['is_active'])

        r = self.client.delete(f'/stock/reports/email-recipients/{pk}/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(StockReportEmailRecipient.objects.count(), 0)

    def test_skip_when_no_recipients(self):
        result = send_closing_stock_report(as_of=date(2026, 8, 20))
        self.assertTrue(result['skipped'])
        self.assertGreaterEqual(result['row_count'], 1)

    def test_csv_and_send(self):
        StockReportEmailRecipient.objects.create(email='a@example.com')
        StockReportEmailRecipient.objects.create(
            email='b@example.com',
            is_active=False,
        )
        with patch(
            'stock_ledger.util.closing_stock_email.send_email_with_attachment',
            return_value='msg-1',
        ) as send:
            result = send_closing_stock_report(as_of=date(2026, 8, 20))
        self.assertEqual(result['recipients'], ['a@example.com'])
        self.assertEqual(result['message_id'], 'msg-1')
        self.assertIn(b'product_name', rows_to_csv([{'product_name': 'x'}]))
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs['to_addresses'], ['a@example.com'])
        self.assertTrue(kwargs['filename'].endswith('.csv'))
        self.assertIn('Gazeboo Cloud', kwargs['body_html'])
        self.assertIn('cid:gazebo-logo', kwargs['body_html'])
        self.assertIn('Good Morning', kwargs['body_html'])
        self.assertIn('Unsubscribe', kwargs['body_html'])
        self.assertIn('email-unsubscribe', kwargs['body_html'])
        self.assertNotIn(self.product.name, kwargs['body_html'])

    def test_html_builder(self):
        html_body = build_closing_stock_html(
            as_of=date(2026, 8, 20),
            unsubscribe_href='http://example/unsub',
        )
        self.assertIn('Gazeboo Cloud', html_body)
        self.assertIn('Good Morning', html_body)
        self.assertIn('Kind Regards', html_body)
        self.assertIn('20 Aug 2026', html_body)
        self.assertIn('cid:gazebo-logo', html_body)
        self.assertIn('Unsubscribe', html_body)
        self.assertNotIn('<thead>', html_body)

    def test_unsubscribe_link(self):
        row = StockReportEmailRecipient.objects.create(email='a@example.com')
        r = self.client.get(
            '/stock/reports/email-unsubscribe/',
            {'token': unsubscribe_token(row.id)},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'unsubscribed', r.content.lower())
        row.refresh_from_db()
        self.assertFalse(row.is_active)

    def test_command_dry_run_and_ses_error(self):
        StockReportEmailRecipient.objects.create(email='a@example.com')
        call_command('email_closing_stock_report', as_of='2026-08-20', dry_run=True)
        from django.core.management.base import CommandError

        with patch(
            'stock_ledger.util.closing_stock_email.send_email_with_attachment',
            side_effect=SesMailError('SES_FROM_EMAIL is not configured.'),
        ):
            with self.assertRaises(CommandError):
                call_command('email_closing_stock_report', as_of='2026-08-20')
