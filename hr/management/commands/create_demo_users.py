"""Management command to generate demo accounts for local prototype exploration.

Strictly prohibited outside development (aborts if DJANGO_DEBUG=0).
Generates random one-time passwords, prints them once to stdout, and sets
must_change_password=True on UserProfile.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from hr.accounts import generate_one_time_password, one_time_password_expiry
from hr.audit import disable_audit
from hr.models import Office, Person, UserProfile
from hr.permissions import (
    ROLE_AUDITOR,
    ROLE_EXECUTIVE,
    ROLE_HRMO,
    ROLE_OFFICE_HEAD,
    ROLE_PROJECT_MAINTAINER,
    ROLE_SYSADMIN,
)


class Command(BaseCommand):
    help = "Generates demo user accounts across all municipal roles with random one-time passwords (DEV ONLY)."

    def handle(self, *args, **options):
        # 1. Safety guard: Abort if not in DEBUG mode
        if not getattr(settings, "DEBUG", False):
            raise CommandError("create_demo_users is strictly restricted to development environments (DJANGO_DEBUG=1).")

        with disable_audit():
            # Ensure roles and standard offices exist
            call_command("setup_roles", verbosity=0)

            User = get_user_model()

            # Ensure we have sample offices and sample person
            hrmo_office = Office.objects.filter(acronym="HRMO").first() or Office.objects.first()
            mo_office = Office.objects.filter(acronym="MO").first() or Office.objects.first()
            mto_office = Office.objects.filter(acronym="MTO").first() or Office.objects.first()
            sample_person = Person.objects.first()

            roles_config = [
                {
                    "username": "demo_hrmo",
                    "role_name": "HRMO Staff",
                    "group": ROLE_HRMO,
                    "is_staff": True,
                    "office": hrmo_office,
                    "person": None,
                    "description": "Full HR management, appointment drafting, records access",
                },
                {
                    "username": "demo_head",
                    "role_name": "Office Head",
                    "group": ROLE_OFFICE_HEAD,
                    "is_staff": False,
                    "office": mto_office,
                    "person": None,
                    "description": f"Office Head scoped to {mto_office.name if mto_office else 'Office'}",
                },
                {
                    "username": "demo_exec",
                    "role_name": "Executive",
                    "group": ROLE_EXECUTIVE,
                    "is_staff": False,
                    "office": mo_office,
                    "person": None,
                    "description": "Mayor / Municipal Administrator approval role",
                },
                {
                    "username": "demo_auditor",
                    "role_name": "Auditor",
                    "group": ROLE_AUDITOR,
                    "is_staff": False,
                    "office": None,
                    "person": None,
                    "description": "Read-only auditor with access to rates and audit log",
                },
                {
                    "username": "demo_projects",
                    "role_name": "Project Maint.",
                    "group": ROLE_PROJECT_MAINTAINER,
                    "is_staff": False,
                    "office": None,
                    "person": None,
                    "description": "Planning / Budget: creates and edits projects, no personnel access",
                },
                {
                    "username": "demo_admin",
                    "role_name": "System Admin",
                    "group": ROLE_SYSADMIN,
                    "is_staff": True,
                    "office": None,
                    "person": None,
                    "description": "IT administrator managing auth users/groups in /admin/",
                },
                {
                    "username": "demo_employee",
                    "role_name": "Employee",
                    "group": None,
                    "is_staff": False,
                    "office": None,
                    "person": sample_person,
                    "description": f"Employee self-service linked to {sample_person.full_name if sample_person else 'Sample Person'}",
                },
            ]

            created_accounts = []

            for cfg in roles_config:
                username = cfg["username"]
                user, _ = User.objects.get_or_create(
                    username=username,
                    defaults={"email": f"{username}@sanpascual.gov.ph.fake"},
                )
                user.is_staff = cfg["is_staff"]

                password = generate_one_time_password()
                user.set_password(password)
                user.save()

                if cfg["group"]:
                    group = Group.objects.get(name=cfg["group"])
                    user.groups.set([group])
                else:
                    user.groups.clear()

                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.office = cfg["office"]
                profile.person = cfg["person"]
                profile.must_change_password = True
                profile.password_expires_at = one_time_password_expiry()
                profile.save()

                created_accounts.append({
                    "username": username,
                    "role": cfg["role_name"],
                    "password": password,
                    "target": str(cfg["office"] or (cfg["person"].full_name if cfg["person"] else "—")),
                    "desc": cfg["description"],
                })

            self.stdout.write("\n" + "=" * 80)
            self.stdout.write("MUNICIPALITY OF SAN PASCUAL - PROTOTYPE DEMO USERS (DEVELOPMENT ONLY)")
            self.stdout.write("Random one-time passwords generated. Password change forced on first login.")
            self.stdout.write("=" * 80)
            self.stdout.write(f"{'Role':<15} {'Username':<16} {'Password':<16} {'Linked Entity':<25}")
            self.stdout.write("-" * 80)
            for acc in created_accounts:
                self.stdout.write(f"{acc['role']:<15} {acc['username']:<16} {acc['password']:<16} {acc['target']:<25}")
            self.stdout.write("=" * 80)
            self.stdout.write("IMPORTANT: Passwords are shown once and never logged. Re-run to reset credentials.\n")
