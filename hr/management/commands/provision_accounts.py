"""Creates employee self-service accounts in bulk (DECISIONS A1, A4, D9).

For every person with an active appointment and no account yet:
- username = employee number (e.g. SP-00042)
- a random one-time password, changed at first login (MustChangePasswordMiddleware)
  and refused after ONE_TIME_PASSWORD_DAYS if never used (OneTimePasswordBackend)
- no roles: an Employee account sees only its own record. Office Head, HRMO and every
  other role is assigned separately by the emergency superuser (R3).

Passwords are shown once, on screen and optionally as a printable slips page, and are
never stored: the database keeps only the PBKDF2 hash, and the audit log records each
new account with the password redacted. Print the slips, hand them out in person, then
delete the file.

Usage (owner role, "manage" service):
    python manage.py provision_accounts --dry-run
    python manage.py provision_accounts --office MTO --slips account_slips_MTO.html
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.html import escape
from hr.accounts import generate_one_time_password, one_time_password_expiry
from hr.models import Engagement, Office, Person, UserProfile


class Command(BaseCommand):
    help = "Creates self-service accounts with one-time passwords for people with an active appointment."

    def add_arguments(self, parser):
        parser.add_argument("--office", help="Only people active in this office (acronym or ID).")
        parser.add_argument("--dry-run", action="store_true", help="List who would get an account; create nothing.")
        parser.add_argument("--slips", metavar="PATH", help="Also write a printable page of password slips here.")

    def handle(self, *args, **options):
        User = get_user_model()
        office = self._resolve_office(options.get("office"))

        active = Q(engagements__status=Engagement.Status.ACTIVE)
        if office:
            active &= Q(engagements__office=office)
        people = list(
            Person.objects.filter(active, user_profile__isnull=True).distinct().order_by("employee_no")
        )

        # An existing account that already uses the employee number but is not linked to
        # this person needs a human decision; never take it over silently.
        taken = set(
            User.objects.filter(username__in=[p.employee_no for p in people]).values_list("username", flat=True)
        )
        conflicts = [p for p in people if p.employee_no in taken]
        to_create = [p for p in people if p.employee_no not in taken]

        scope = f" in {office.name}" if office else ""
        self.stdout.write(f"{len(to_create)} account(s) to create{scope}.")
        for person in conflicts:
            self.stdout.write(self.style.WARNING(
                f"  Skipped {person.employee_no} ({person.full_name}): username already used by an "
                "account not linked to this person. Link or rename it in /admin/ first."
            ))
        if options["dry_run"]:
            for person in to_create:
                self.stdout.write(f"  {person.employee_no}  {person.full_name}")
            self.stdout.write("Dry run: nothing was created.")
            return
        if not to_create:
            return

        # Hash first, outside the transaction: PBKDF2 is deliberately slow, and every audit
        # row written in the transaction holds the audit chain lock until it commits.
        self.stdout.write("Generating one-time passwords...")
        issued = []
        for i, person in enumerate(to_create, start=1):
            password = generate_one_time_password()
            issued.append((person, password, make_password(password)))
            if i % 50 == 0:
                self.stdout.write(f"  {i}/{len(to_create)}")

        expires_at = one_time_password_expiry()
        with transaction.atomic():
            for person, _, password_hash in issued:
                user = User(
                    username=person.employee_no,
                    first_name=person.first_name[:150],
                    last_name=person.last_name[:150],
                    email=person.email or "",
                    password=password_hash,
                )
                user.save()
                UserProfile.objects.create(
                    user=user, person=person, must_change_password=True, password_expires_at=expires_at
                )

        valid_until = timezone.localtime(expires_at).strftime("%Y-%m-%d %H:%M")
        self.stdout.write(self.style.SUCCESS(
            f"Created {len(issued)} account(s). One-time passwords work until {valid_until} if unused."
        ))
        self.stdout.write(f"{'Employee no':<12} {'Name':<36} One-time password")
        for person, password, _ in issued:
            self.stdout.write(f"{person.employee_no:<12} {person.full_name[:36]:<36} {password}")

        if options.get("slips"):
            self._write_slips(options["slips"], issued, valid_until)
            self.stdout.write(self.style.WARNING(
                f"Slips written to {options['slips']}. Print them, hand them out in person, then delete the file."
            ))

    def _resolve_office(self, value):
        if not value:
            return None
        office = Office.objects.filter(Q(acronym__iexact=value) | Q(pk=int(value) if value.isdigit() else None)).first()
        if not office:
            raise CommandError(f"No office with acronym or ID '{value}'.")
        return office

    def _write_slips(self, path, issued, valid_until):
        slips = "\n".join(
            f"""<div class="slip">
  <div class="org">Municipality of San Pascual · HR Records</div>
  <div class="name">{escape(person.full_name)}</div>
  <div class="row"><span>Username</span><code>{escape(person.employee_no)}</code></div>
  <div class="row"><span>One-time password</span><code>{escape(password)}</code></div>
  <p>Log in and set your own password right away. This password stops working on
  {escape(valid_until)} if unused. Never share it; HRMO will never ask for it.</p>
</div>"""
            for person, password, _ in issued
        )
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Account slips</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 12mm; color: #000; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; }}
  .slip {{ border: 1px dashed #555; padding: 5mm; break-inside: avoid; font-size: 11pt; }}
  .org {{ font-size: 9pt; text-transform: uppercase; letter-spacing: .05em; }}
  .name {{ font-weight: 700; margin: 2mm 0 3mm; }}
  .row {{ display: flex; justify-content: space-between; margin: 1mm 0; }}
  code {{ font-size: 13pt; font-weight: 700; letter-spacing: .08em; }}
  p {{ font-size: 9pt; margin: 3mm 0 0; }}
</style></head>
<body><div class="grid">
{slips}
</div></body></html>
"""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
