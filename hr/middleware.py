"""Audit and security middleware for San Pascual HR records.

- Binds request context to thread-local ContextVar for signal-based audit logging.
- Automatically logs HTTP 403 Permission Denied events on protected resources (DECISIONS U1/R2).
"""

from .audit import log_audit_event, set_current_request
from .models import AuditLog


class AuditMiddleware:
    """Captures request context and records 403 permission denials."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_current_request(request)
        try:
            response = self.get_response(request)

            # Log HTTP 403 permission denials
            if response.status_code == 403:
                log_audit_event(
                    action=AuditLog.Action.PERMISSION_DENIED,
                    model_name="Permission",
                    object_repr=f"HTTP 403 Forbidden: {request.method} {request.path}",
                    changes={"path": request.path, "method": request.method},
                    request=request,
                )

            return response
        finally:
            set_current_request(None)
