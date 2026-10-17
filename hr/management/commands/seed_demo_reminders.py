"""Gives the fake data something for the renewal and retirement lists to show (DEV ONLY,
DECISIONS RM5).

The fake people are all under 62 and their contracts end in December, so the lists start empty.
Relative to today, this:
- moves two Job Order contracts' end dates to 10 days ago (still active: "lapsed") and 20 days ahead;
- makes one plantilla employee 65 last month and another turn 65 in three months;
- makes one department head 61 with an earlier, ended casual appointment from 15 years ago,
  so they can retire optionally;
- adds a sample quarterly meeting for everyone from January: held in the earlier quarters,
  missed last quarter, and not yet scheduled this quarter (added once, by name).
Changed rows are tagged "[demo]" in their notes and the command does nothing a second time.
Runs with auditing off, like the other seeders.
"""

from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from hr.audit import disable_audit
from hr.meetings import ensure_meetings, period_of
from hr.models import Engagement, MeetingSeries
from hr.salary_actions import add_years

TAG = "[demo reminders]"
SAMPLE_MEETING = "Quarterly HR meeting (sample)"


class Command(BaseCommand):
    help = "Adds lapsed contracts and retirement cases relative to today for a demo (DEV ONLY)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_reminders is for demos only (DJANGO_DEBUG=1).")
        today = timezone.localdate()
        self._sample_meeting(today)
        if Engagement.objects.filter(notes__contains=TAG).exists():
            self.stdout.write("Demo reminders already added.")
            return
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

    def _sample_meeting(self, today):
        if MeetingSeries.objects.filter(name=SAMPLE_MEETING).exists():
            return
        with disable_audit(), transaction.atomic():
            series = MeetingSeries.objects.create(
                name=SAMPLE_MEETING, legal_basis="[demo] Ask HRMO for the actual circular",
                starts_on=today.replace(month=1, day=1),
            )
            ensure_meetings(today, MeetingSeries.objects.filter(pk=series.pk))
            current_start, _end = period_of(today, 3)
            past = [m for m in series.meetings.order_by("period_start") if m.period_end < current_start]
            for meeting in past[:-1]:  # the last finished quarter is left as missed
                held = meeting.period_end - timedelta(days=20)
                meeting.scheduled_on = meeting.held_on = held
                meeting.attendance_count = 180
                meeting.minutes_location = "HRMO cabinet B, meetings folder"
                meeting.save()
