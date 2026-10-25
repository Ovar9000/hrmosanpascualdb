"""Audit logging engine for San Pascual HR records.

Features:
- Immutable append-only logging (enforced by DB triggers in migration).
- Transaction-bound writes: if audit logging fails, the action transaction fails (DECISIONS U3).
- Data minimization: Sensitive fields (rate, fake IDs, passwords) log field names only, never values (DECISIONS U4).
- No foreign keys: snapshot user_id, username, office_id, office_name to allow clean user deletions.
- Client IP resolution: walks X-Forwarded-For right to left, only past TRUSTED_PROXIES hops (DECISIONS U5).
- Suppressible for data seeders (seed_fake, seed_offices) via disable_audit() context manager.
- SHA-256 hash chaining (prev_hash, row_hash), appends serialized by an advisory lock.
"""

import contextvars
import hashlib
import ipaddress
import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from django.utils.ipv6 import clean_ipv6_address

# Request ID validation: only accepted from trusted proxy if matching ^[A-Za-z0-9-]{8,64}$
REQUEST_ID_REGEX = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# Key of the transaction-scoped advisory lock that serializes audit chain appends.
AUDIT_CHAIN_LOCK_KEY = 0x53504155  # "SPAU"

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
    # Personal Data Sheet (DECISIONS PD6): ID numbers, answers to questions 34-40, and the frozen copy.
    "gov_id_number",
    "license_number",
    "snapshot",
    "q34_details", "q35a_details", "q35b_details", "q35b_status", "q36_details", "q37_details",
    "q38a_details", "q38b_details", "q39_details", "q40a_details", "q40b_details", "q40c_details",
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


def normalize_ip(value):
    """Returns the IP in the exact text form PostgreSQL stores and returns, or None if invalid.

    The row hash is computed before the INSERT and re-checked after reading the row back,
    so a non-canonical spelling (e.g. upper-case IPv6) would break the chain.
    """
    if not value:
        return None
    try:
        ip = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return None
    if ip.version == 6:
        if ip.scope_id:
            return None  # zone IDs ("%eth0") are not valid in an inet column
        return clean_ipv6_address(str(ip))
    return str(ip)


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
    """Returns the client IP (also used by django-axes via AXES_CLIENT_IP_CALLABLE).

    X-Forwarded-For is read only when REMOTE_ADDR is a trusted proxy, and right to left:
    each proxy appends the address it received the request from, so the first hop that is
    not a trusted proxy is the client. Entries left of it were sent by the client and can
    be forged. A malformed hop stops the walk at the last address we could trust.

    If CLIENT_IP_HEADER is set (e.g. "X-Real-IP" on Railway, whose edge overwrites it with the
    connecting address), a trusted proxy's value in that header is used instead.
    """
    if not request:
        return None
    client_ip = normalize_ip(request.META.get("REMOTE_ADDR"))
    if not client_ip or not is_ip_trusted(client_ip):
        return client_ip

    header = getattr(settings, "CLIENT_IP_HEADER", "")
    if header:
        meta_key = "HTTP_" + header.upper().replace("-", "_")
        header_ip = normalize_ip(request.META.get(meta_key))
        if header_ip:
            return header_ip

    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    for hop in reversed([h for h in xff.split(",") if h.strip()]):
        hop_ip = normalize_ip(hop)
        if hop_ip is None:
            break
        client_ip = hop_ip
        if not is_ip_trusted(hop_ip):
            break
    return client_ip


def resolve_request_id(request):
    """Extracts or generates request ID.
    
    Accepts X-Request-ID ONLY when REMOTE_ADDR is in TRUSTED_PROXIES and matches ^[A-Za-z0-9-]{8,64}$.
    Otherwise generates a fresh UUID4.
    """
    if not request:
        return uuid.uuid4().hex
    existing_id = getattr(request, "request_id", None)
    if existing_id and REQUEST_ID_REGEX.match(existing_id):
        return existing_id

    remote_addr = request.META.get("REMOTE_ADDR")
    raw_header = request.headers.get("X-Request-ID") if hasattr(request, "headers") else request.META.get("HTTP_X_REQUEST_ID", "")
    if raw_header and is_ip_trusted(remote_addr) and REQUEST_ID_REGEX.match(raw_header):
        return raw_header
    return uuid.uuid4().hex


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
    ip_address = normalize_ip(ip_address)

    # Request ID and Session Key Hash (Requirement 3)
    request_id = ""
    session_key_hash = ""
    if req:
        request_id = resolve_request_id(req)
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

    with transaction.atomic():
        # Serialize appends, then read the chain head in a new statement so READ COMMITTED
        # sees the row the previous lock holder committed. Locking the last row with
        # SELECT ... FOR UPDATE is not enough: a waiter gets the old head back and both
        # writers link to it (and on an empty table there is no row to lock).
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [AUDIT_CHAIN_LOCK_KEY])
        last_entry = AuditLog.objects.order_by("-id").only("row_hash").first()
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


class VerificationResult(tuple):
    """Result tuple (is_valid, count, error_msg, broken_id) with extra attributes anchor_verified and last_id."""

    def __new__(cls, is_valid, count, error_msg, broken_id, anchor_verified=False, last_id=None):
        instance = super().__new__(cls, (is_valid, count, error_msg, broken_id))
        instance.is_valid = is_valid
        instance.count = count
        instance.error_msg = error_msg
        instance.broken_id = broken_id
        instance.anchor_verified = anchor_verified
        instance.last_id = last_id
        return instance


def parse_anchor(anchor):
    """Parses an anchor in format 'ID:HASH' or tuple (ID, HASH).

    Returns:
        tuple: (int, str) or (None, None)
    """
    if not anchor:
        return None, None
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        try:
            return int(anchor[0]), str(anchor[1])
        except (ValueError, TypeError):
            return None, None
    if isinstance(anchor, str) and ":" in anchor:
        parts = anchor.split(":", 1)
        try:
            return int(parts[0].strip()), parts[1].strip()
        except (ValueError, TypeError):
            return None, None
    return None, None


def verify_chain(anchor=None):
    """Verifies the SHA-256 hash chain across all AuditLog entries with a full scan from genesis.

    If anchor 'ID:HASH' is supplied, verification fails when that row is missing or its hash differs.
    This is expensive on a large table: call it from the management command or the rate-limited
    "Re-verify all" action, never on page load.

    Returns:
        VerificationResult: (is_valid: bool, count: int, error_msg: str, broken_id: int | None)
        with attributes .anchor_verified: bool and .last_id: int | None
    """
    from .models import AuditLog

    expected_anchor_id, expected_anchor_hash = parse_anchor(anchor)
    if anchor and expected_anchor_id is None:
        return VerificationResult(
            False,
            0,
            f"Invalid anchor format '{anchor}'. Expected format ID:HASH (e.g. 10:abc123...)",
            None,
        )

    count = 0
    prev_expected = "0" * 64
    last_id = None
    anchor_found = False

    for entry in AuditLog.objects.order_by("id").iterator():
        count += 1
        if entry.prev_hash != prev_expected:
            return VerificationResult(
                False,
                count,
                f"Broken link at record #{entry.pk}: prev_hash mismatch. "
                f"Expected '{prev_expected}', found '{entry.prev_hash}'.",
                entry.pk,
                last_id=last_id,
            )

        expected_row_hash = entry.calculate_row_hash(prev_hash=entry.prev_hash)
        if entry.row_hash != expected_row_hash:
            return VerificationResult(
                False,
                count,
                f"Tampered record at #{entry.pk}: row_hash mismatch. "
                f"Expected '{expected_row_hash}', found '{entry.row_hash}'.",
                entry.pk,
                last_id=last_id,
            )

        if expected_anchor_id is not None and entry.pk == expected_anchor_id:
            if entry.row_hash != expected_anchor_hash:
                return VerificationResult(
                    False,
                    count,
                    f"Anchor hash mismatch at row #{entry.pk}: "
                    f"expected '{expected_anchor_hash}', found '{entry.row_hash}'.",
                    entry.pk,
                    last_id=last_id,
                )
            anchor_found = True

        prev_expected = entry.row_hash
        last_id = entry.pk

    if expected_anchor_id is not None and not anchor_found:
        return VerificationResult(
            False,
            count,
            f"Anchor row #{expected_anchor_id} not found in audit log.",
            expected_anchor_id,
            last_id=last_id,
        )

    return VerificationResult(True, count, "", None, anchor_found, last_id=last_id)


def run_and_record_verification(anchor=None, source="command", username=""):
    """Runs a full verify_chain() scan and stores the result as the single AuditChainStatus row."""
    from .models import AuditChainStatus

    result = verify_chain(anchor=anchor)
    if result.anchor_verified:
        anchor_status = AuditChainStatus.AnchorStatus.VERIFIED
    elif anchor:
        anchor_status = AuditChainStatus.AnchorStatus.NOT_VERIFIED
    else:
        anchor_status = AuditChainStatus.AnchorStatus.NONE

    AuditChainStatus.objects.update_or_create(
        pk=1,
        defaults={
            "verified_at": timezone.now(),
            "is_valid": result.is_valid,
            "record_count": result.count,
            "last_record_id": result.last_id,
            "broken_id": result.broken_id,
            "error": result.error_msg,
            "anchor": str(anchor or "")[:100],
            "anchor_status": anchor_status,
            "source": source,
            "verified_by": username or "",
        },
    )
    return result
