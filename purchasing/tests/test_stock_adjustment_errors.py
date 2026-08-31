"""Stock adjustment API error message mapping."""

from django.test import SimpleTestCase

from purchasing.views import _stock_adjustment_error_message


class StockAdjustmentErrorMessageTests(SimpleTestCase):
    def test_conversion_error_is_user_friendly(self):
        raw = 'No stock_unit_conversion for unit_id=7, product_id=167'
        msg = _stock_adjustment_error_message(raw)
        self.assertIn('Liter-to-kg', msg)
        self.assertIn('unitary weight', msg)
        self.assertNotIn('unit_id=', msg)

    def test_other_errors_pass_through(self):
        self.assertEqual(
            _stock_adjustment_error_message('use_by is required.'),
            'use_by is required.',
        )
