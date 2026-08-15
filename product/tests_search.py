from django.test import TestCase

from locations.models import Location, LocationRole, LocationRoleAssignment
from product.models import Category, Product, ProductClass, ProductGoodsInType, Unit
from purchasing.models import PurchaseOrderStatus
from purchasing.services.po import create_purchase_order
from stock_ledger.models import StockLot, StockLotOrigin


class GlobalSearchApiTests(TestCase):
    def setUp(self):
        ProductClass.objects.create(id=1, name='Packed')
        Category.objects.create(id=1, name='Meals')
        Unit.objects.create(id=1, name='Kg')
        self.wh = Location.objects.create(id=1, name='WH', visible=True)
        self.supplier = Location.objects.create(
            id=2, name='Chicken Co', visible=True,
        )
        LocationRoleAssignment.objects.create(
            location=self.supplier, role=LocationRole.SUPPLIER,
        )
        self.raw = Product.objects.create(
            name='Chicken thigh raw',
            product_class_id=1,
            category_id=1,
            unit_id=1,
            source_container=self.wh,
            destination_container=self.wh,
            goods_in_type=ProductGoodsInType.RAW_MATERIAL,
        )
        self.pack = Product.objects.create(
            name='Chicken thigh pack',
            product_class_id=1,
            category_id=1,
            unit_id=1,
            source_container=self.wh,
            destination_container=self.wh,
            goods_in_type=ProductGoodsInType.PACKAGING,
        )
        self.other = Product.objects.create(
            name='Salt 1kg',
            product_class_id=1,
            category_id=1,
            unit_id=1,
            source_container=self.wh,
            destination_container=self.wh,
        )

    def _hits(self, q: str):
        resp = self.client.get('/search/', {'q': q})
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()['data']['results']

    def test_short_q_rejected(self):
        resp = self.client.get('/search/', {'q': 'x'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('at least 2', resp.json()['message'])

    def test_chicken_hits_raw_and_pack(self):
        hits = self._hits('ch')
        product_hits = [h for h in hits if h['type'] == 'product']
        ids = {h['id'] for h in product_hits}
        self.assertIn(self.raw.id, ids)
        self.assertIn(self.pack.id, ids)
        self.assertNotIn(self.other.id, ids)
        types = {h['goods_in_type'] for h in product_hits}
        self.assertIn(ProductGoodsInType.RAW_MATERIAL, types)
        self.assertIn(ProductGoodsInType.PACKAGING, types)

        listed = self.client.get('/product/', {'q': 'chicken'})
        self.assertEqual(listed.status_code, 200)
        list_ids = {r['id'] for r in listed.json()['data']}
        self.assertEqual(list_ids, {self.raw.id, self.pack.id})

    def test_po_via_line_product_name(self):
        po = create_purchase_order(
            supplier_id=self.supplier.id,
            lines=[{'product_id': self.raw.id, 'qty_ordered': '4'}],
            status=PurchaseOrderStatus.DRAFT,
        )
        hits = self._hits('chicken')
        po_hits = [h for h in hits if h['type'] == 'purchase_order']
        self.assertEqual(len(po_hits), 1)
        self.assertEqual(po_hits[0]['id'], po.id)
        self.assertEqual(po_hits[0]['subtitle'], 'Chicken Co')

    def test_scan_shaped_q_prepends_scan_hit(self):
        hits = self._hits(f'P{self.other.id}')
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0]['type'], 'scan')
        self.assertEqual(hits[0]['id'], self.other.id)
        self.assertEqual(hits[0]['match_type'], 'product')

    def test_stock_lot_by_product_name(self):
        lot = StockLot.objects.create(
            product=self.raw,
            trace_number='T-CHX-1',
            origin=StockLotOrigin.PURCHASE,
        )
        hits = self._hits('chicken')
        stock_hits = [h for h in hits if h['type'] == 'stock']
        self.assertEqual(len(stock_hits), 1)
        self.assertEqual(stock_hits[0]['id'], lot.id)
        self.assertEqual(stock_hits[0]['label'], 'T-CHX-1')
