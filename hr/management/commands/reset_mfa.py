"""Resets an account's two-step login after a lost or replaced phone (DECISIONS A5).

Removes the authenticator app and the recovery codes; the person sets up a new app at their next
login. Check the person's ID first, as for a password reset (A2). Audit-logged as MFA_RESET.
Also the way back in for the emergency superuser, who has no one above them in /admin/.

Usage (owner role, "manage" service):
    python manage.py reset_mfa SP-00042 --reason "Phone lost, LGU ID checked by J. Cruz"
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from hr import mfa


class Command(BaseCommand):
    help = "Removes an account's authenticator app and recovery codes (lost phone)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--reason", required=True, help="Why, and whose ID was checked. Goes into the audit log.")

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(username__iexact=options["username"]).first()
        if user is None:
            raise CommandError(f"No account named {options['username']}.")
        if not mfa.reset(user, options["reason"], actor_name="manage.py reset_mfa"):
            self.stdout.write(f"{user.username} had no authenticator app; recovery codes (if any) removed.")
            return
        self.stdout.write(self.style.SUCCESS(
            f"Two-step login reset for {user.username}. They set up a new app at their next login."
        ))
