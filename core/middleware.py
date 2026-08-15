from core.http_audit import audit_request
from core.ops import record_exception


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
