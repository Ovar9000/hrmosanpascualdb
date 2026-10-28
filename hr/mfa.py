"""Second login step with an authenticator app (DECISIONS A5).

TOTP (RFC 6238): the app on the person's phone (Google Authenticator, Microsoft Authenticator,
Aegis, ...) and the server share a secret, and each works out a 6-digit code from it and the
current 30-second window. Nothing is sent by email or SMS, so it needs no mail server or SMS
gateway, and the phone needs no signal or data. Ten one-use recovery codes cover a lost phone.

The session is marked verified (SESSION_VERIFIED) only after a code is accepted; MfaMiddleware
keeps an unverified session away from every page but these. A new login clears the mark.

"Remember this browser" trades a little safety for fewer codes, since sessions end after 30 idle
minutes: a signed cookie lets that browser skip the code for MFA_REMEMBER_DAYS. The password is
still needed every time, and the cookie stops working on a password change, a reset of the app,
or "Forget remembered browsers".
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from .audit import log_audit_event
from .models import AuditLog, MfaDevice, MfaRecoveryCode

ISSUER = "San Pascual HR"
STEP_SECONDS = 30
DIGITS = 6
# Codes from the window before or after are accepted too: phone clocks drift.
DRIFT_STEPS = 1
# A refused code that matches a window up to this far off (10 minutes) means a wrong clock, on the
# phone or the server: the page says so instead of "not right". Such codes are still refused.
CLOCK_CHECK_STEPS = 20
# Wrong codes in a row before the account's second step locks for LOCK_MINUTES. The lock is per
# account, not per session: a fresh password login does not buy more guesses.
MAX_FAILURES = 5
LOCK_MINUTES = 15
RECOVERY_CODE_COUNT = 10
# No look-alike characters (0/o, 1/l/i): the codes are printed or written down and typed back.
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

SESSION_VERIFIED = "mfa_verified"
SESSION_PENDING_SECRET = "mfa_pending_secret"
# One cookie per account, so two people sharing a browser never pass on each other's cookie.
REMEMBER_COOKIE_PREFIX = "sp_mfa_trust_"
REMEMBER_SALT = "hr.mfa.remember"


# --- Who needs it -------------------------------------------------------------------------------


def mfa_required_for(user):
    """Accounts that must set up the second step: superusers, /admin/ staff and MFA_REQUIRED_ROLES.

    Off when MFA_REQUIRED is off (the fake-data review demo, H3). Anyone who set it up anyway
    still has to pass it: has_device() decides that, not this.
    """
    if not settings.MFA_REQUIRED or not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.is_staff or user.groups.filter(name__in=settings.MFA_REQUIRED_ROLES).exists()


def has_device(user):
    return MfaDevice.objects.filter(user=user).exists()


def is_verified(request):
    return request.session.get(SESSION_VERIFIED) == request.user.pk


def needs_second_step(request):
    """True while this logged-in session must still pass (or set up) the second step."""
    user = request.user
    if not user.is_authenticated or is_verified(request):
        return False
    return has_device(user) or mfa_required_for(user)


def mark_verified(request):
    """New session key on the way up, like a login, so a key seen before the second step is useless."""
    request.session.cycle_key()
    request.session[SESSION_VERIFIED] = request.user.pk
    request.session.pop(SESSION_PENDING_SECRET, None)


# --- Remembered browsers -----------------------------------------------------------------------


def remember_cookie_name(user):
    return f"{REMEMBER_COOKIE_PREFIX}{user.pk}"


def _remember_claims(user, device):
    # The session auth hash changes with the password, so a password change forgets every browser.
    return {"u": user.pk, "d": device.pk, "g": device.remember_generation, "h": user.get_session_auth_hash()}


def remember_browser(response, user):
    """Sets the cookie that lets this browser skip the code for MFA_REMEMBER_DAYS (A5)."""
    device = MfaDevice.objects.filter(user=user).first()
    if settings.MFA_REMEMBER_DAYS <= 0 or device is None:
        return
    response.set_cookie(
        remember_cookie_name(user), signing.dumps(_remember_claims(user, device), salt=REMEMBER_SALT),
        max_age=settings.MFA_REMEMBER_DAYS * 86400, secure=settings.SESSION_COOKIE_SECURE, httponly=True, samesite="Lax",
    )


def is_remembered(request):
    """True when this browser holds a valid, unexpired remember cookie for the logged-in account."""
    token = request.COOKIES.get(remember_cookie_name(request.user))
    if settings.MFA_REMEMBER_DAYS <= 0 or not token:
        return False
    try:
        claims = signing.loads(token, salt=REMEMBER_SALT, max_age=settings.MFA_REMEMBER_DAYS * 86400)
    except signing.BadSignature:  # also expired
        return False
    device = MfaDevice.objects.filter(user=request.user).first()
    if device is None or not isinstance(claims, dict):
        return False
    expected = _remember_claims(request.user, device)
    return all(claims.get(k) == expected[k] for k in ("u", "d", "g")) and hmac.compare_digest(
        str(claims.get("h", "")), expected["h"]
    )


def pass_remembered(request):
    mark_verified(request)
    user = request.user
    log_audit_event(
        action=AuditLog.Action.MFA_PASSED, model_name="User", object_id=str(user.pk), user=user,
        object_repr=f"{user.get_username()} passed the second login step (remembered browser)",
        changes={"method": "remembered browser"},
    )


def forget_browsers(user):
    """Every browser remembered so far asks for a code again."""
    with transaction.atomic():
        device = MfaDevice.objects.select_for_update().get(user=user)
        device.remember_generation += 1
        device.save()
        log_audit_event(
            action=AuditLog.Action.MFA_SETUP, model_name="User", object_id=str(user.pk), user=user,
            object_repr=f"Remembered browsers forgotten for {user.get_username()}",
            changes={"remembered_browsers": "forgotten"},
        )


# --- TOTP ---------------------------------------------------------------------------------------


def new_secret():
    """160 random bits, the size RFC 4226 recommends, as base32 for the app."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP_SECONDS)


def code_at(secret, step):
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def clean_code(raw):
    # Any space, not just " ": a code copied from some apps or phones comes with a non-breaking one.
    return "".join(ch for ch in (raw or "") if not ch.isspace() and ch != "-").lower()


def matching_step(secret, raw_code, after_step=0, now=None):
    """The window whose code this is, or None. Windows up to `after_step` were used already."""
    code = clean_code(raw_code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if candidate > after_step and hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def used_already(secret, raw_code, last_used_step, now=None):
    """True for a right code from a window already used, such as the one just typed at setup or login.

    Still refused (a code works once), but it is no wrong guess: the app keeps showing that code for
    up to 30 seconds, so someone who logs out and straight back in types it again.
    """
    code = clean_code(raw_code)
    if len(code) != DIGITS or not code.isdigit():
        return False
    step = current_step(now)
    return any(
        candidate <= last_used_step and hmac.compare_digest(code_at(secret, candidate), code)
        for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1)
    )


def clock_offset(secret, raw_code, now=None):
    """Seconds the app's clock is ahead of the server's (negative: behind), or None.

    Only for a code outside the accepted windows that matches one within CLOCK_CHECK_STEPS: then the
    code is right but the phone and the server disagree on the time. Never accepted, only explained.
    """
    code = clean_code(raw_code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for distance in range(DRIFT_STEPS + 1, CLOCK_CHECK_STEPS + 1):
        for candidate in (step + distance, step - distance):
            if hmac.compare_digest(code_at(secret, candidate), code):
                return (candidate - step) * STEP_SECONDS
    return None


def provisioning_uri(secret, username):
    label = quote(f"{ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP_SECONDS}"


def qr_data_uri(uri):
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#000", light="#fff")


def grouped(secret):
    """For typing by hand: ABCD EFGH ..."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# --- Recovery codes -----------------------------------------------------------------------------


def _hash_recovery(code):
    # A plain hash is enough: the codes are random (about 49 bits), and anyone who can read this
    # table can read the TOTP secret next to it anyway.
    return hashlib.sha256(clean_code(code).encode("ascii")).hexdigest()


def new_recovery_codes(user):
    """Replaces the account's recovery codes. Returns them: shown once, never stored."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    MfaRecoveryCode.objects.filter(user=user).delete()
    for code in codes:
        MfaRecoveryCode.objects.create(user=user, code_hash=_hash_recovery(code))
    return codes


def replace_recovery_codes(user):
    """New codes asked for from Two-step login: the old ones stop working."""
    with transaction.atomic():
        codes = new_recovery_codes(user)
        log_audit_event(
            action=AuditLog.Action.MFA_SETUP, model_name="User", object_id=str(user.pk),
            object_repr=f"New recovery codes for {user.get_username()}", changes={"recovery_codes": "replaced"},
            user=user,
        )
    return codes


def recovery_codes_left(user):
    return MfaRecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def _use_recovery_code(user, raw_code):
    cleaned = clean_code(raw_code)
    if len(cleaned) != 10:
        return False
    return MfaRecoveryCode.objects.filter(
        user=user, code_hash=_hash_recovery(cleaned), used_at__isnull=True
    ).update(used_at=timezone.now()) == 1


# --- Setup, check, reset ------------------------------------------------------------------------


def enroll(user, secret, raw_code):
    """Stores the app if `raw_code` comes from `secret`. Returns the recovery codes, or None."""
    step = matching_step(secret, raw_code)
    if step is None:
        return None
    with transaction.atomic():
        MfaDevice.objects.update_or_create(user=user, defaults={
            "secret": secret, "confirmed_at": timezone.now(), "last_used_step": step, "failures": 0, "locked_until": None,
        })
        codes = new_recovery_codes(user)
        log_audit_event(
            action=AuditLog.Action.MFA_SETUP, model_name="User", object_id=str(user.pk),
            object_repr=f"Two-step login set up for {user.get_username()}", user=user,
        )
    return codes


class Locked(Exception):
    def __init__(self, until):
        super().__init__(until)
        self.until = until


class CodeUsed(Exception):
    """The right code, but its window was used already: wait for the next one. Not counted as wrong."""


class ClockOff(Exception):
    """The right code for a time `offset` seconds away: a clock is wrong. Refused, not counted as wrong."""

    def __init__(self, offset):
        super().__init__(offset)
        self.offset = offset


def check_code(user, raw_code, allow_recovery=True):
    """Checks a code from the app (or a recovery code) and records the outcome.

    Returns "app" or "recovery code" when accepted, None when not. Raises Locked while the
    account's second step is locked, without looking at the code, CodeUsed for a code that was
    already used and ClockOff for a code from a clock up to 10 minutes off (both refused, but not
    counted towards the lock).
    """
    with transaction.atomic():
        device = MfaDevice.objects.select_for_update().get(user=user)
        now = timezone.now()
        if device.locked_until and device.locked_until > now:
            raise Locked(device.locked_until)
        method = None
        step = matching_step(device.secret, raw_code, after_step=device.last_used_step)
        if step is not None:
            device.last_used_step, method = step, "app"
        elif allow_recovery and _use_recovery_code(user, raw_code):
            method = "recovery code"
        target = {"model_name": "User", "object_id": str(user.pk), "user": user}
        if method:
            device.failures, device.locked_until = 0, None
            device.save()
            changes = {"method": method}
            if method == "recovery code":
                changes["recovery_codes_left"] = recovery_codes_left(user)
            log_audit_event(
                action=AuditLog.Action.MFA_PASSED, changes=changes,
                object_repr=f"{user.get_username()} passed the second login step ({method})", **target,
            )
            return method
        used, locked = used_already(device.secret, raw_code, device.last_used_step), False
        offset = None if used else clock_offset(device.secret, raw_code)
        if used:
            log_audit_event(
                action=AuditLog.Action.MFA_FAILED, changes={"locked": False, "reason": "code already used"},
                object_repr=f"Second-step code used again for {user.get_username()}", **target,
            )
        elif offset is not None:
            log_audit_event(
                action=AuditLog.Action.MFA_FAILED, changes={"locked": False, "reason": f"clocks differ by {offset} s"},
                object_repr=f"Second-step code from a clock {offset:+d} s off for {user.get_username()}", **target,
            )
        else:
            device.failures += 1
            locked = device.failures >= MAX_FAILURES
            if locked:
                device.failures, device.locked_until = 0, now + timedelta(minutes=LOCK_MINUTES)
            device.save()
            log_audit_event(
                action=AuditLog.Action.MFA_FAILED, changes={"locked": locked},
                object_repr=f"Wrong second-step code for {user.get_username()}" + (" (locked)" if locked else ""), **target,
            )
    if used:
        raise CodeUsed()
    if offset is not None:
        raise ClockOff(offset)
    if locked:
        raise Locked(device.locked_until)
    return None


def reset(user, reason, actor=None, actor_name=None):
    """Removes the app and recovery codes (lost phone). The person sets it up again at next login."""
    with transaction.atomic():
        removed, _ = MfaDevice.objects.filter(user=user).delete()
        MfaRecoveryCode.objects.filter(user=user).delete()
        log_audit_event(
            action=AuditLog.Action.MFA_RESET, model_name="User", object_id=str(user.pk),
            object_repr=f"Two-step login reset for {user.get_username()}",
            changes={"reason": reason[:200], "had_app": bool(removed)},
            user=actor, username=actor_name,
        )
    return bool(removed)
