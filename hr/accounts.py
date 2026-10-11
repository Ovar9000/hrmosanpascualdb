"""One-time passwords for accounts created or reset by an administrator (DECISIONS A1, A4).

A one-time password is random, must be changed at first login (MustChangePasswordMiddleware),
and stops working after ONE_TIME_PASSWORD_DAYS if it was never used (OneTimePasswordBackend).
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
