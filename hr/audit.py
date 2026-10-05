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


def calculate_hash(prev_hash, timestamp_str, user_id, action, model_name, object_id, changes_dict):
    """Calculates SHA-256 hash for optional tamper-evident chaining."""
    serialized_changes = json.dumps(changes_dict, sort_keys=True)
    payload = f"{prev_hash}|{timestamp_str}|{user_id}|{action}|{model_name}|{object_id}|{serialized_changes}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    """Creates an AuditLog record in the active transaction.

    If audit creation fails, the exception propagates so the enclosing action fails.
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

    # Fetch latest hash for optional chain
    prev_entry = AuditLog.objects.order_by("-id").only("row_hash").first()
    prev_hash = prev_entry.row_hash if prev_entry else "0" * 64
    now_str = datetime.now().isoformat()
    row_hash = calculate_hash(
        prev_hash, now_str, user_id, action, model_name, str(object_id), safe_changes
    )

    # Write in current transaction (fails the action if this fails)
    entry = AuditLog.objects.create(
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
        prev_hash=prev_hash,
        row_hash=row_hash,
    )
    return entry
