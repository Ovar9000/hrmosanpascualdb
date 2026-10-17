"""Gives the fake data a few long careers so the Salary Actions tracker has loyalty awards to
show (DEV ONLY, DECISIONS SA6).

The fake people all start in 2019 or later, so nobody reaches 10 years. For four department
heads this adds an earlier, ended Casual appointment in the same office, timed so their 10-year
mark falls between a month ago and two months from now. It also records one leave without pay
for a plantilla employee, so a due date moves. Tagged "[demo]" and added once; then the check runs.
Runs with auditing off, like the other seeders.
"""

from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from hr.audit import disable_audit
from hr.models import Engagement, ServiceInterruption
from hr.salary_actions import add_years, run_check

DEMO_TAG = "[demo]"
OFFSETS = (-30, -5, 20, 60)  # days from today to each head's 10-year mark


class Command(BaseCommand):
    help = "Adds earlier service for a few heads and one LWOP so the tracker shows loyalty awards (DEV ONLY)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_salary_history is for demos only (DJANGO_DEBUG=1).")
        today = timezone.localdate()
        added = 0
        with disable_audit(), transaction.atomic():
            heads = (
                Engagement.objects.filter(status=Engagement.Status.ACTIVE, item_number__endswith="-HD-01")
                .select_related("person", "office").order_by("office__name")[: len(OFFSETS)]
            )
            for head, offset in zip(heads, OFFSETS):
                if head.person.engagements.filter(notes__startswith=DEMO_TAG).exists():
                    continue
                Engagement.objects.create(
                    person=head.person, office=head.office, type=Engagement.Type.CASUAL,
                    position_title="Administrative Aide VI (Casual)",
                    start_date=add_years(today + timedelta(days=offset), -10),
                    end_date=head.start_date - timedelta(days=1),
                    status=Engagement.Status.ENDED, leave_eligible=True,
                    notes=f"{DEMO_TAG} Earlier service for the loyalty award demo.",
                )
                added += 1
            due = (
                Engagement.objects.filter(status=Engagement.Status.ACTIVE, type=Engagement.Type.PLANTILLA)
                .exclude(item_number__endswith="-HD-01").order_by("start_date", "pk").first()
            )
            if due and not ServiceInterruption.objects.filter(notes__startswith=DEMO_TAG).exists():
                start = due.start_date + timedelta(days=200)
                ServiceInterruption.objects.create(
                    person=due.person, kind=ServiceInterruption.Kind.LWOP,
                    start_date=start, end_date=start + timedelta(days=24),
                    notes=f"{DEMO_TAG} Sample leave without pay.",
                )
        created, updated = run_check(source="command")
        self.stdout.write(f"Added {added} earlier appointment(s); check opened {created} case(s), updated {updated}.")
