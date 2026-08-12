"""PO detail serializer includes RBAC display names."""

from datetime import date
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from locations.models import Location, LocationRole, LocationRoleAssignment
from purchasing.models import PurchaseOrder, PurchaseOrderStatus
from purchasing.serialize import po_detail_dict
from users_rbac.models import RbacUser


class PoDetailCheckedByNameTests(TestCase):
    def test_po_detail_includes_checked_by_name(self):
        user = RbacUser.objects.create(
            cognito_sub=f'sub-{uuid4().hex[:8]}',
            username='checker01',
            display_name='Alex Checker',
        )
        wh = Location.objects.create(id=901, name='WH', visible=True)
        supplier = Location.objects.create(id=902, name='Supplier', visible=True)
        LocationRoleAssignment.objects.create(
            location=supplier, role=LocationRole.SUPPLIER,
        )
        po = PurchaseOrder.objects.create(
            number=f'PO-{uuid4().hex[:6]}',
            supplier=supplier,
            ship_to_location=wh,
            status=PurchaseOrderStatus.ORDERED,
            ordered_at=date.today(),
            checked_by_user_id=user.id,
            checked_at=timezone.now(),
            external_number='SAGE-TEST-1',
        )

        data = po_detail_dict(po)

        self.assertEqual(data['checked_by_user_id'], user.id)
        self.assertEqual(data['checked_by_name'], 'Alex Checker')
        self.assertEqual(data['sage_po_number'], 'SAGE-TEST-1')
        self.assertIsNone(data['qc_tl_checked_by_name'])
        self.assertIsNone(data['created_by_name'])
