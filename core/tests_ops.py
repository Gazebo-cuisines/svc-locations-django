import json
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from core.http_audit import _redact
from core.middleware import OpsErrorMiddleware
from core.models import ErrorTicket, ErrorTicketStatus
from core.ops import record_error
from users_rbac.models import AdminAccess, AdminArea, Department, RbacUser, UserDepartment


class ErrorTicketTests(TestCase):
    def setUp(self):
        self.floor = RbacUser.objects.create(
            cognito_sub='sub-floor',
            username='floor01',
            display_name='Floor',
        )
        self.admin = RbacUser.objects.create(
            cognito_sub='sub-admin',
            username='admin01',
            display_name='Admin',
        )
        UserDepartment.objects.create(user=self.admin, department=Department.ADMIN)
        AdminAccess.objects.create(user=self.admin, area=AdminArea.TECHNICAL)
        self.audit = patch('core.http_audit._start_audit')
        self.mock_audit = self.audit.start()
        self.addCleanup(self.audit.stop)

    def _attach(self, user):
        def fake(request, **kwargs):
            request.rbac_user = user
            return None

        return patch('users_rbac.auth.attach_user', side_effect=fake)

    def test_post_creates_then_dedupes(self):
        body = json.dumps({'message': 'boom', 'stack': 'Error: boom\n at App.js:1'})
        with self._attach(self.floor):
            first = self.client.post(
                '/ops/errors/', data=body, content_type='application/json'
            )
            second = self.client.post(
                '/ops/errors/', data=body, content_type='application/json'
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        ticket = ErrorTicket.objects.get()
        self.assertEqual(ticket.occurrences, 2)
        self.assertEqual(ticket.actor_username, 'floor01')

    def test_post_requires_message(self):
        with self._attach(self.floor):
            response = self.client.post(
                '/ops/errors/',
                data=json.dumps({}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 400)

    def test_floor_cannot_list(self):
        with self._attach(self.floor):
            response = self.client.get('/ops/errors/')
        self.assertEqual(response.status_code, 403)

    def test_admin_lists_and_patches(self):
        ticket = record_error(message='boom', stack='y')
        with self._attach(self.admin):
            listed = self.client.get('/ops/errors/?status=open')
            patched = self.client.patch(
                f'/ops/errors/{ticket.id}/',
                data=json.dumps({'status': 'investigating', 'note': 'looking'}),
                content_type='application/json',
            )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()['data']), 1)
        self.assertEqual(patched.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, ErrorTicketStatus.INVESTIGATING)
        self.assertEqual(ticket.note, 'looking')

    def test_resolved_reopens(self):
        ticket = record_error(message='boom', stack='y')
        ticket.status = ErrorTicketStatus.RESOLVED
        ticket.save(update_fields=['status'])
        record_error(message='boom', stack='y')
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, ErrorTicketStatus.OPEN)
        self.assertEqual(ticket.occurrences, 2)

    def test_middleware_records_server_error(self):
        request = RequestFactory().get('/product/99/')
        request.rbac_user = self.floor
        OpsErrorMiddleware(lambda req: None).process_exception(
            request, RuntimeError('kaboom')
        )
        ticket = ErrorTicket.objects.get()
        self.assertEqual(ticket.source, 'server')
        self.assertIn('kaboom', ticket.message)
        self.assertEqual(ticket.url, '/product/99/')

    def test_middleware_skips_ops_path(self):
        request = RequestFactory().post('/ops/errors/')
        OpsErrorMiddleware(lambda req: None).process_exception(
            request, RuntimeError('nope')
        )
        self.assertEqual(ErrorTicket.objects.count(), 0)

    def test_redacts_secrets(self):
        self.assertEqual(
            _redact({'password': 'x', 'name': 'jane', 'id_token': 't'}),
            {'password': '[redacted]', 'name': 'jane', 'id_token': '[redacted]'},
        )

    def test_http_audit_captures_in_and_out(self):
        with self._attach(self.floor):
            self.client.post(
                '/ops/errors/',
                data=json.dumps({'message': 'boom', 'payload': {'ref': 1}}),
                content_type='application/json',
            )
        payload = self.mock_audit.call_args.args[0]
        self.assertEqual(payload['method'], 'POST')
        self.assertEqual(payload['path'], '/ops/errors/')
        self.assertEqual(payload['in']['message'], 'boom')
        self.assertEqual(payload['out']['status'], 'success')
        self.assertEqual(payload['status'], 201)

    def test_http_audit_skips_get(self):
        with self._attach(self.admin):
            self.client.get('/ops/errors/')
        self.mock_audit.assert_not_called()

    def test_client_error_logged_to_journal(self):
        from unittest.mock import patch

        from core.http_audit import _log_client_error
        from django.http import JsonResponse
        from django.test import RequestFactory

        req = RequestFactory().post('/purchasing/stock-adjustment/')
        resp = JsonResponse(
            {'status': 'error', 'message': 'No stock_unit_conversion', 'data': None},
            status=400,
        )
        with patch('core.http_audit.logger') as log:
            _log_client_error(req, resp)
            log.warning.assert_called_once()
            self.assertIn('No stock_unit_conversion', log.warning.call_args.args[-1])
