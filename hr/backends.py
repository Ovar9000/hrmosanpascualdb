from django.contrib.auth.backends import ModelBackend
from django.utils import timezone
from .models import UserProfile


class OneTimePasswordBackend(ModelBackend):
    """ModelBackend that refuses a one-time password once it has expired unused (DECISIONS A4).

    The login fails like a wrong password (and counts toward the axes lockout), so the page
    does not reveal that the password itself was right. HRMO issues a new one.
    """

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False
        return not UserProfile.objects.filter(
            user=user, must_change_password=True, password_expires_at__lt=timezone.now()
        ).exists()
