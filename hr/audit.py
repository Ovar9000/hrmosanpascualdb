"""Audit logging engine for San Pascual HR records.

Features:
- Immutable append-only logging (enforced by DB triggers in migration).
- Transaction-bound writes: if audit logging fails, the action transaction fails (DECISIONS U3).
- Data minimization: Sensitive fields (rate, fake IDs, passwords) log field names only, never values (DECISIONS U4).
- No foreign keys: snapshot user_id, username, office_id, office_name to allow clean user deletions.
- Client IP resolution: reads X-Forwarded-For strictly when REMOTE_ADDR is in TRUSTED_PROXIES (DECISIONS U5).
- Suppressible for data seeders (seed_fake, seed_offices) via disable_audit() context manager.
- Optional cryptographic SHA-256 hash chaining (prev_hash, row_hash).
"""

import contextvars
import hashlib
import ipaddress
import json
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone

# Thread-safe context variables
_audit_disabled_var = contextvars.ContextVar("audit_disabled", default=False)
_audit_request_var = contextvars.ContextVar("audit_request", default=None)

SENSITIVE_AUDIT_FIELDS = {
    "rate",
    "tin",
    "gsis_no",
    "philhealth_no",
    "pagibig_no",
    "sss_no",
    "password",
}


def is_audit_disabled():
    return _audit_disabled_var.get()


@contextmanager
def disable_audit():
    """Context manager to suppress audit logging (used by seed_fake, seed_offices, etc.)."""
    token = _audit_disabled_var.set(True)
    try:
        yield
    finally:
        _audit_disabled_var.reset(token)


def set_current_request(request):
    return _audit_request_var.set(request)


def get_current_request():
    return _audit_request_var.get()


def is_ip_trusted(ip_str):
    """Verifies whether an IP matches the configured TRUSTED_PROXIES."""
    if not ip_str:
        return False
    trusted_list = getattr(settings, "TRUSTED_PROXIES", ["127.0.0.1", "::1"])
    try:
        remote_ip = ipaddress.ip_address(ip_str.strip())
        for pattern in trusted_list:
            pattern = pattern.strip()
            if not pattern:
                continue
            if "/" in pattern:
                if remote_ip in ipaddress.ip_network(pattern, strict=False):
                    return True
            else:
                if remote_ip == ipaddress.ip_address(pattern):
                    return True
    except ValueError:
        return False
    return False


def get_client_ip(request):
    """Extracts client IP. Inspects X-Forwarded-For ONLY if REMOTE_ADDR is a trusted proxy."""
    if not request:
        return None
    remote_addr = request.META.get("REMOTE_ADDR")
    if not remote_addr:
        return None

    if is_ip_trusted(remote_addr):
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            ips = [ip.strip() for ip in xff.split(",") if ip.strip()]
            if ips:
                return ips[0]
    return remote_addr


def serialize_field_value(val):
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, (int, float, bool, str)):
        return val
    return str(val)


def redact_changes_diff(raw_changes):
    """Ensures sensitive fields (pay, IDs, passwords) never store values in audit logs."""
    cleaned = {}
    for field_name, diff in raw_changes.items():
        if field_name in SENSITIVE_AUDIT_FIELDS:
            # Field name is preserved; values are completely omitted / redacted
            cleaned[field_name] = "[REDACTED]"
        else:
            cleaned[field_name] = diff
    return cleaned


def log_audit_event(
    action,
    model_name="",
    object_id="",
    object_repr="",
    changes=None,
    user=None,
    username=None,
    ip_address=None,
    office=None,
    request=None,
):
    """Creates an AuditLog record in the active transaction with race-safe hash chaining.

    If audit creation fails, the exception propagates so the enclosing action fails (DECISIONS U3).
    """
    if is_audit_disabled():
        return None

    # Resolve request context
    req = request or get_current_request()

    user_id = None
    if user and getattr(user, "is_authenticated", False):
        user_id = user.pk
        username = username or user.get_username()
    elif req and getattr(req, "user", None) and req.user.is_authenticated:
        user_id = req.user.pk
        username = username or req.user.get_username()

    if not ip_address and req:
        ip_address = get_client_ip(req)

    # Request ID and Session Key Hash (Requirement 3)
    request_id = ""
    session_key_hash = ""
    if req:
        request_id = getattr(req, "request_id", "") or req.META.get("HTTP_X_REQUEST_ID", "")
        if hasattr(req, "session") and req.session.session_key:
            session_key_hash = hashlib.sha256(req.session.session_key.encode("utf-8")).hexdigest()

    office_id = None
    office_name = ""
    if office:
        office_id = getattr(office, "pk", None)
        office_name = getattr(office, "name", str(office))
    elif user and hasattr(user, "profile") and user.profile.office:
        office_id = user.profile.office.pk
        office_name = user.profile.office.name
    elif req and hasattr(req, "user") and hasattr(req.user, "profile") and req.user.profile.office:
        office_id = req.user.profile.office.pk
        office_name = req.user.profile.office.name

    # Redact sensitive fields
    safe_changes = redact_changes_diff(changes or {})

    # Circular import guard
    from .models import AuditLog

    # Lock latest row and compute row_hash with last row locked in active transaction
    with transaction.atomic():
        last_entry = AuditLog.objects.select_for_update().order_by("-id").first()
        prev_hash = last_entry.row_hash if (last_entry and last_entry.row_hash) else ("0" * 64)

        now = timezone.now()
        entry_mock = AuditLog(
            timestamp=now,
            action=action,
            user_id=user_id,
            username=username or "",
            office_id=office_id,
            office_name=office_name,
            model_name=model_name,
            object_id=str(object_id) if object_id is not None else "",
            object_repr=str(object_repr)[:255] if object_repr else "",
            changes=safe_changes,
            ip_address=ip_address,
            request_id=request_id,
            session_key_hash=session_key_hash,
            prev_hash=prev_hash,
        )
        row_hash = entry_mock.calculate_row_hash(prev_hash=prev_hash)

        return AuditLog.objects.create(
            timestamp=now,
            action=action,
            user_id=user_id,
            username=username or "",
            office_id=office_id,
            office_name=office_name,
            model_name=model_name,
            object_id=str(object_id) if object_id is not None else "",
            object_repr=str(object_repr)[:255] if object_repr else "",
            changes=safe_changes,
            ip_address=ip_address,
            request_id=request_id,
            session_key_hash=session_key_hash,
            prev_hash=prev_hash,
            row_hash=row_hash,
        )


def verify_chain():
    """Verifies the integrity of the cryptographic SHA-256 hash chain across all AuditLog entries.

    Returns:
        tuple: (is_valid: bool, count: int, error_msg: str, broken_id: int | None)
    """
    from .models import AuditLog

    count = 0
    prev_expected = "0" * 64

    for entry in AuditLog.objects.order_by("id").iterator():
        count += 1
        # 1. Verify previous hash pointer
        if entry.prev_hash != prev_expected:
            return (
                False,
                count,
                f"Broken link at record #{entry.pk}: prev_hash mismatch. "
                f"Expected '{prev_expected}', found '{entry.prev_hash}'.",
                entry.pk,
            )

        # 2. Verify row contents hash
        expected_row_hash = entry.calculate_row_hash(prev_hash=entry.prev_hash)
        if entry.row_hash != expected_row_hash:
            return (
                False,
                count,
                f"Tampered record at #{entry.pk}: row_hash mismatch. "
                f"Expected '{expected_row_hash}', found '{entry.row_hash}'.",
                entry.pk,
            )

        prev_expected = entry.row_hash

    return (True, count, "", None)
