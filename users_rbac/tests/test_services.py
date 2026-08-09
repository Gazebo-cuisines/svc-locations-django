from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.db import IntegrityError
from django.test import Client, TestCase

from users_rbac.models import (
    Department,
    ProductionAccess,
    ProductionArea,
    RbacAuditAction,
    RbacAuditEvent,
    RbacUser,
    UserDepartment,
    WarehouseAccess,
    WarehouseUnit,
)
from users_rbac.services import create_identity, login, reset_password, set_active


class RbacSchemaTests(TestCase):
    def test_user_grants_and_audit_row(self):
        user = RbacUser.objects.create(
            cognito_sub='sub-amit',
            username='amit01',
            display_name='Amit',
        )
        UserDepartment.objects.create(user=user, department=Department.PRODUCTION)
        ProductionAccess.objects.create(user=user, area=ProductionArea.LOW_RISK)
        WarehouseAccess.objects.create(
            user=user,
            unit=WarehouseUnit.UNIT_1,
            can_goods_in=True,
            goods_in_today=True,
        )
        event = RbacAuditEvent.objects.create(
            action=RbacAuditAction.AUTH_LOGIN_SUCCESS,
            actor_sub=user.cognito_sub,
            actor_username=user.username,
            target_user=user,
            source_ip='10.0.1.22',
        )
        self.assertEqual(str(user), 'amit01')
        self.assertEqual(user.departments.count(), 1)
        self.assertEqual(user.production_access.get().area, ProductionArea.LOW_RISK)
        self.assertTrue(user.warehouse_access.get().can_goods_in)
        self.assertEqual(event.action, RbacAuditAction.AUTH_LOGIN_SUCCESS)

    def test_duplicate_department_rejected(self):
        user = RbacUser.objects.create(cognito_sub='sub-1', username='jane')
        UserDepartment.objects.create(user=user, department=Department.ADMIN)
        with self.assertRaises(IntegrityError):
            UserDepartment.objects.create(user=user, department=Department.ADMIN)


def _client_error(code: str) -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': code}}, 'op')


def _cognito_user(sub: str, *, email: str | None = None) -> dict:
    attrs = [{'Name': 'sub', 'Value': sub}]
    if email:
        attrs.append({'Name': 'email', 'Value': email})
    return {'User': {'Attributes': attrs}, 'UserAttributes': attrs}


class CognitoIdentityTests(TestCase):
    def setUp(self):
        self.env = patch.dict(
            'os.environ',
            {
                'COGNITO_USER_POOL_ID': 'eu-west-2_testpool',
                'COGNITO_CLIENT_ID': 'client-id',
                'COGNITO_CLIENT_SECRET': 'client-secret',
                'COGNITO_REGION': 'eu-west-2',
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_create_identity_without_email(self):
        mock_client = MagicMock()
        mock_client.admin_create_user.return_value = _cognito_user('sub-amit')
        with patch('users_rbac.services._client', return_value=mock_client):
            user = create_identity('amit01', 'Secret123!', display_name='Amit')
        self.assertEqual(user.username, 'amit01')
        self.assertIsNone(user.email)
        self.assertEqual(user.cognito_sub, 'sub-amit')
        mock_client.admin_create_user.assert_called_once()
        kwargs = mock_client.admin_create_user.call_args.kwargs
        self.assertEqual(kwargs['Username'], 'amit01')
        self.assertEqual(kwargs['MessageAction'], 'SUPPRESS')
        self.assertEqual(kwargs['UserAttributes'], [])
        mock_client.admin_set_user_password.assert_called_once()
        self.assertTrue(mock_client.admin_set_user_password.call_args.kwargs['Permanent'])

    def test_create_identity_with_email(self):
        mock_client = MagicMock()
        mock_client.admin_create_user.return_value = _cognito_user(
            'sub-jane', email='jane@gazebo.test'
        )
        with patch('users_rbac.services._client', return_value=mock_client):
            user = create_identity(
                'jane.admin',
                'Secret123!',
                email='jane@gazebo.test',
                display_name='Jane',
            )
        self.assertEqual(user.email, 'jane@gazebo.test')
        names = [a['Name'] for a in mock_client.admin_create_user.call_args.kwargs['UserAttributes']]
        self.assertIn('email', names)

    def test_create_identity_duplicate_username(self):
        mock_client = MagicMock()
        mock_client.admin_create_user.side_effect = _client_error('UsernameExistsException')
        with patch('users_rbac.services._client', return_value=mock_client):
            with self.assertRaises(ValueError) as ctx:
                create_identity('amit01', 'Secret123!')
        self.assertIn('already in use', str(ctx.exception))

    def test_set_active_disable_and_enable(self):
        user = RbacUser.objects.create(cognito_sub='sub-1', username='amit01')
        mock_client = MagicMock()
        with patch('users_rbac.services._client', return_value=mock_client):
            set_active(user, False)
            set_active(user, True)
        mock_client.admin_disable_user.assert_called_once()
        mock_client.admin_enable_user.assert_called_once()
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_reset_password(self):
        user = RbacUser.objects.create(cognito_sub='sub-1', username='amit01')
        mock_client = MagicMock()
        with patch('users_rbac.services._client', return_value=mock_client):
            reset_password(user, 'NewSecret123!')
        mock_client.admin_set_user_password.assert_called_once()
        self.assertTrue(mock_client.admin_set_user_password.call_args.kwargs['Permanent'])

    def test_login_invalid_credentials(self):
        mock_client = MagicMock()
        mock_client.initiate_auth.side_effect = _client_error('NotAuthorizedException')
        with patch('users_rbac.services._client', return_value=mock_client):
            with self.assertRaises(ValueError) as ctx:
                login('amit01', 'wrong')
        self.assertEqual(str(ctx.exception), 'Invalid username or password.')


class LoginViewTests(TestCase):
    def test_missing_credentials(self):
        response = Client().post(
            '/auth/login/',
            data=b'{}',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Username or email', response.json()['message'])
