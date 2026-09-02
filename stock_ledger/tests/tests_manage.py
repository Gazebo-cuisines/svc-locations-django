import json
import time
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client, TestCase
from django.utils import timezone

from locations.models import Location
from product.models import Category, Product, ProductClass, Range, Unit
from stock_ledger.models import StockEntryPostingStatus, StockLot, StockLotOrigin
from stock_ledger.util import entry_posting, services
from users_rbac.models import (
    AdminAccess,
    AdminArea,
    Department,
    RbacUser,
    UserDepartment,
)

POOL = 'eu-west-2_testpool'
CLIENT_ID = 'client-id'
ISSUER = f'https://cognito-idp.eu-west-2.amazonaws.com/{POOL}'
ENV = {
    'COGNITO_USER_POOL_ID': POOL,
    'COGNITO_CLIENT_ID': CLIENT_ID,
    'COGNITO_REGION': 'eu-west-2',
}


def _token(private_key, *, sub: str):
    return jwt.encode(
        {
            'sub': sub,
            'iss': ISSUER,
            'token_use': 'id',
            'aud': CLIENT_ID,
            'exp': int(time.time()) + 3600,
        },
        private_key,
        algorithm='RS256',
        headers={'kid': 'test-kid'},
    )


class StockManageApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.env = patch.dict('os.environ', ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.key_patch = patch(
            'users_rbac.auth._get_signing_key',
            return_value=private_key.public_key(),
        )
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)

        self.admin = RbacUser.objects.create(
            cognito_sub='sub-mgr',
            username='mgr01',
            display_name='Manager',
        )
        UserDepartment.objects.create(user=self.admin, department=Department.ADMIN)
        AdminAccess.objects.create(user=self.admin, area=AdminArea.STOCK_MANAGEMENT)
        self.auth = f'Bearer {_token(private_key, sub="sub-mgr")}'

        Location.objects.create(id=1, name='Unit 2', visible=True)
        Location.objects.create(id=2, name='Low Risk', visible=True)
        ProductClass.objects.create(id=1, name='Finished')
        Category.objects.create(id=1, name='Meals')
        Range.objects.create(id=1, name='Main')
        Unit.objects.create(id=1, name='Kg')
        self.product = Product.objects.create(
            name='Flour',
            product_class_id=1,
            category_id=1,
            range_id=1,
            unit_id=1,
            source_container_id=1,
            destination_container_id=2,
        )
        self.lot = StockLot.objects.create(
            product=self.product,
            trace_number=f'T{uuid4().hex[:8]}',
            origin=StockLotOrigin.PURCHASE,
            production_date=date(2026, 8, 1),
            use_by=date(2026, 9, 1),
        )

    def test_ping_and_preview_cancel(self):
        ping = self.client.get('/stock/manage/ping/', HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(ping.status_code, 200)

        entry = services.receipt(
            idempotency_key=f'mgr-{uuid4()}',
            lot=self.lot,
            location_id=1,
            quantity=Decimal('10'),
            unit_id=1,
            effective_at=timezone.now(),
            actor_user_id=self.admin.id,
            lan_username='mgr01',
        )
        entry_posting.queue_entry(entry=entry, actor_user_id=self.admin.id)
        preview = self.client.get(
            f'/stock/manage/entries/{entry.id}/',
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(preview.status_code, 200, preview.content)
        data = preview.json()['data']
        self.assertEqual(data['preview']['action'], 'cancel')

        remove = self.client.post(
            f'/stock/manage/entries/{entry.id}/remove/',
            data=json.dumps({
                'reason': 'test cancel',
                'idempotency_key': f'rm-{uuid4()}',
            }),
            content_type='application/json',
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(remove.status_code, 200, remove.content)
        body = remove.json()['data']
        self.assertIn(f'E{entry.id}', body['cancelled_entry_codes'])
        entry.refresh_from_db()
        self.assertEqual(entry.posting.status, StockEntryPostingStatus.CANCELLED)

    def test_ping_forbidden_without_admin(self):
        floor = RbacUser.objects.create(
            cognito_sub='sub-floor',
            username='floor01',
            display_name='Floor',
        )
        UserDepartment.objects.create(user=floor, department=Department.WAREHOUSE)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with patch(
            'users_rbac.auth._get_signing_key',
            return_value=private_key.public_key(),
        ):
            auth = f'Bearer {_token(private_key, sub="sub-floor")}'
            resp = self.client.get('/stock/manage/ping/', HTTP_AUTHORIZATION=auth)
        self.assertEqual(resp.status_code, 403)
