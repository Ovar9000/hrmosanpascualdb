"""Audit and security middleware for San Pascual HR records.

- Binds request context to thread-local ContextVar for signal-based audit logging.
- Automatically logs HTTP 403 Permission Denied events on protected resources (DECISIONS U1/R2).
- De-duplicates PERMISSION_DENIED entries (at most one per user + path + minute).
- Holds sessions at the second login step until a code is accepted (DECISIONS A5).
- Forces users flagged with must_change_password to the password-change page (DECISIONS A1).
- Answers the hosting platform's health probe at /healthz/.
"""

from datetime import timedelta
from urllib.parse import urlencode
from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from . import mfa
from .audit import log_audit_event, resolve_request_id, set_current_request
from .models import AuditLog, UserProfile


class HealthCheckMiddleware:
    """Returns 200 "ok" at /healthz/ when the database answers, 503 otherwise.

    Listed first in MIDDLEWARE: Railway probes the container over plain HTTP with its own
    Host header, so the probe must be answered before ALLOWED_HOSTS and the HTTPS redirect
    apply. Reveals nothing beyond "ok", and is not audited.
    """

    PATHS = ("/healthz", "/healthz/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path not in self.PATHS:
            return self.get_response(request)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except DatabaseError:
            return HttpResponse("database unavailable", status=503, content_type="text/plain")
        return HttpResponse("ok", content_type="text/plain")


class AuditMiddleware:
    """Captures request context, assigns request_id, and records 403 permission denials."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = resolve_request_id(request)
        token = set_current_request(request)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request.request_id

            # Log HTTP 403 permission denials (de-duplicated: 1 per user + path + minute)
            if response.status_code == 403:
                one_minute_ago = timezone.now() - timedelta(minutes=1)
                user = getattr(request, "user", None)
                user_id = user.pk if user and user.is_authenticated else None
                username = user.get_username() if user and user.is_authenticated else ""

                already_logged = AuditLog.objects.filter(
                    action=AuditLog.Action.PERMISSION_DENIED,
                    user_id=user_id,
                    username=username,
                    changes__path=request.path,
                    timestamp__gte=one_minute_ago,
                ).exists()

                if not already_logged:
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


class MfaMiddleware:
    """Keeps a session that has not passed the second login step away from every page (DECISIONS A5).

    Fail-closed: a session counts as verified only once a code was accepted in it, or the browser
    holds a valid "remember this browser" cookie. Until then an account with an app goes to the
    code page, and an account that must have one goes to setup.
    Exempt: those two pages, logout and static files. Runs before MustChangePasswordMiddleware,
    so a stolen password alone cannot even be changed.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not self._is_exempt(request.path) and mfa.needs_second_step(request):
            if mfa.has_device(user):
                if mfa.is_remembered(request):
                    mfa.pass_remembered(request)
                    return self.get_response(request)
                return redirect(f"{reverse('mfa_verify')}?{urlencode({'next': request.get_full_path()})}")
            return redirect("mfa_setup")
        return self.get_response(request)

    @staticmethod
    def _is_exempt(path):
        static_prefix = "/" + settings.STATIC_URL.lstrip("/")
        return path in (reverse("mfa_verify"), reverse("mfa_setup"), reverse("logout")) or path.startswith(static_prefix)


class MustChangePasswordMiddleware:
    """Redirects users with UserProfile.must_change_password to the password-change page.

    Every URL is blocked except password change, logout, and static files. The flag is
    cleared by hr.views.PasswordChangeView after a successful change.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user
            and user.is_authenticated
            and not self._is_exempt(request.path)
            and UserProfile.objects.filter(user=user, must_change_password=True).exists()
        ):
            return redirect("password_change")
        return self.get_response(request)

    @staticmethod
    def _is_exempt(path):
        static_prefix = "/" + settings.STATIC_URL.lstrip("/")
        # The second-step pages stay open: MfaMiddleware runs first and sends people there (A5).
        exempt = ("password_change", "logout", "mfa_verify", "mfa_setup")
        return path in [reverse(name) for name in exempt] or path.startswith(static_prefix)
