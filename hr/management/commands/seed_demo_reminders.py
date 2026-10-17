"""Gives the fake data something for the renewal and retirement lists to show (DEV ONLY,
DECISIONS RM5).

The fake people are all under 62 and their contracts end in December, so the lists start empty.
Relative to today, this:
- moves two Job Order contracts' end dates to 10 days ago (still active: "lapsed") and 20 days ahead;
- makes one plantilla employee 65 last month and another turn 65 in three months;
- makes one department head 61 with an earlier, ended casual appointment from 15 years ago,
  so they can retire optionally.
Changed rows are tagged "[demo]" in their notes and the command does nothing a second time.
Runs with auditing off, like the other seeders.
"""

from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from hr.audit import disable_audit
from hr.models import Engagement
from hr.salary_actions import add_years

TAG = "[demo reminders]"


class Command(BaseCommand):
    help = "Adds lapsed contracts and retirement cases relative to today for a demo (DEV ONLY)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_reminders is for demos only (DJANGO_DEBUG=1).")
        if Engagement.objects.filter(notes__contains=TAG).exists():
            self.stdout.write("Demo reminders already added.")
            return
        today = timezone.localdate()
        active = Engagement.objects.filter(status=Engagement.Status.ACTIVE).select_related("person").order_by("pk")
        with disable_audit(), transaction.atomic():
            for eng, offset in zip(active.filter(type=Engagement.Type.JOB_ORDER, end_date__isnull=False)[:2], (-10, 20)):
                eng.end_date = today + timedelta(days=offset)
                eng.notes = f"{eng.notes}\n{TAG} end date moved for the renewals demo".strip()
                eng.save()
            staff = active.filter(type=Engagement.Type.PLANTILLA).exclude(item_number__endswith="-HD-01")[:2]
            for eng, days_to_65 in zip(staff, (-30, 90)):
                person = eng.person
                person.birthdate = add_years(today + timedelta(days=days_to_65), -65)
                person.save()
                eng.notes = f"{eng.notes}\n{TAG} birthdate moved for the retirement demo".strip()
                eng.save()
            head = active.filter(item_number__endswith="-HD-01").order_by("-office__name").first()
            if head:
                head.person.birthdate = add_years(today, -61)
                head.person.save()
                Engagement.objects.create(
                    person=head.person, office=head.office, type=Engagement.Type.CASUAL,
                    position_title="Administrative Aide IV (Casual)",
                    start_date=add_years(today, -15), end_date=head.start_date - timedelta(days=1),
                    status=Engagement.Status.ENDED, notes=f"{TAG} earlier service for the optional retirement demo",
                )
        self.stdout.write("Added demo renewals (1 lapsed, 1 soon) and retirement cases (past 65, turning 65, optional).")
