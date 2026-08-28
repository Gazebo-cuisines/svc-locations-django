from core.api_response import error_response
from core.http_audit import audit_request
from core.maintenance import maintenance_dict
from core.models import MaintenanceNotice
from core.ops import record_exception


_WRITE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
_LOCK_EXEMPT_PREFIXES = (
    '/ops/maintenance/',
    '/ops/errors/',
    '/auth/login/',
    '/auth/refresh/',
    '/hardware/heartbeat/',
    '/app/version/',
)


class OpsErrorMiddleware:
    """Persist unhandled exceptions as error tickets. Does not change the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        path = request.path or ''
        if path.startswith('/ops/errors'):
            return None
        record_exception(request, exception)
        return None


class MaintenanceWriteLockMiddleware:
    """423 mutating requests while maintenance is on. Banner payload in data."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in _WRITE_METHODS:
            return self.get_response(request)
        path = request.path or ''
        if any(path.startswith(prefix) for prefix in _LOCK_EXEMPT_PREFIXES):
            return self.get_response(request)
        row = MaintenanceNotice.objects.filter(pk=1, is_active=True).first()
        if row is None:
            return self.get_response(request)
        return error_response(
            row.message or 'Please allow us 10 minutes for maintenance.',
            data={'maintenance': maintenance_dict(row)},
            status_code=423,
        )


class ApiAuditMiddleware:
    """Write mutating request + response JSON to AUDIT_S3_BUCKET. Never changes the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = None
        try:
            response = self.get_response(request)
            return response
        finally:
            audit_request(request, response)
