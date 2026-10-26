"""One-time passwords for accounts created or reset by an administrator or HRMO (DECISIONS A1, A2, A4).

A one-time password is random, must be changed at first login (MustChangePasswordMiddleware),
and stops working after ONE_TIME_PASSWORD_DAYS (RESET_PASSWORD_HOURS for a forgotten password)
if it was never used (OneTimePasswordBackend).
"""

import secrets
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from .models import UserProfile

# No look-alike characters (0/O, 1/l/I): slips are read off paper and typed on phones.
ONE_TIME_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_one_time_password(length=12):
    """Random password, about 69 bits of entropy at the default length."""
    while True:
        pwd = "".join(secrets.choice(ONE_TIME_PASSWORD_ALPHABET) for _ in range(length))
        # Mixed classes so the password never looks numeric or all-lowercase.
        if any(c.isdigit() for c in pwd) and any(c.islower() for c in pwd) and any(c.isupper() for c in pwd):
            return pwd


def one_time_password_expiry(now=None):
    return (now or timezone.now()) + timedelta(days=settings.ONE_TIME_PASSWORD_DAYS)


def require_password_change(user, expires_at=None):
    """Marks the user's current password as one-time: change at next login, expires if unused."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.must_change_password = True
    profile.password_expires_at = expires_at or one_time_password_expiry()
    profile.save()
    return profile


# --- Forgotten passwords (DECISIONS A2) -------------------------------------------------------

# At most this many "forgot password" requests are stored per IP address per hour; more get the
# same answer on screen but are not stored, so the HRMO queue cannot be flooded.
MAX_RESET_REQUESTS_PER_IP_PER_HOUR = 5


def record_forgot_password(username, contact, ip_address):
    """Stores a request from the login page's "Forgot password?" form, or nothing.

    Whatever happens, the page shows the same answer, so it never reveals which usernames exist.
    One open request per account is enough: a second one for the same account is not stored.
    """
    from django.contrib.auth import get_user_model
    from .models import PasswordResetRequest

    username = (username or "").strip()[:150]
    if not username:
        return None
    since = timezone.now() - timedelta(hours=1)
    if ip_address and PasswordResetRequest.objects.filter(
        ip_address=ip_address, requested_at__gte=since
    ).count() >= MAX_RESET_REQUESTS_PER_IP_PER_HOUR:
        return None
    user = get_user_model().objects.filter(username__iexact=username).first()
    open_requests = PasswordResetRequest.objects.filter(status=PasswordResetRequest.Status.PENDING)
    if user and open_requests.filter(user=user).exists():
        return None
    if not user and open_requests.filter(user=None, username_entered__iexact=username).exists():
        return None
    return PasswordResetRequest.objects.create(
        username_entered=username, user=user, contact=(contact or "").strip()[:100], ip_address=ip_address,
    )


def issue_reset_password(user, issued_by, id_checked, reset_request=None):
    """HRMO hands out a new one-time password after checking the person's ID (DECISIONS A2).

    The old password stops working at once (and any session logged in with it), the new one
    must be changed at first login and stops working after RESET_PASSWORD_HOURS if unused, and
    a login lockout on the account is lifted so the person can use it right away. Every open
    request for the account is closed. Returns the password: it is shown once and never stored.
    """
    from axes.utils import reset as reset_lockouts
    from .models import PasswordResetRequest

    now = timezone.now()
    password = generate_one_time_password()
    user.set_password(password)
    user.save()
    require_password_change(user, expires_at=now + timedelta(hours=settings.RESET_PASSWORD_HOURS))
    reset_lockouts(username=user.get_username())

    if reset_request is None:
        reset_request = PasswordResetRequest(
            username_entered=user.get_username(), user=user, received_via=PasswordResetRequest.Via.COUNTER
        )
    open_requests = list(PasswordResetRequest.objects.filter(user=user, status=PasswordResetRequest.Status.PENDING))
    if reset_request.pk is None or reset_request not in open_requests:
        open_requests.append(reset_request)
    for req in open_requests:
        req.status = PasswordResetRequest.Status.ISSUED
        req.handled_at, req.handled_by, req.id_checked = now, issued_by, id_checked
        req.save()
    return password


def dismiss_reset_request(reset_request, dismissed_by, note):
    from .models import PasswordResetRequest

    reset_request.status = PasswordResetRequest.Status.DISMISSED
    reset_request.handled_at, reset_request.handled_by, reset_request.note = timezone.now(), dismissed_by, note
    reset_request.save()
