"""Fills the calendar around today so a demo opens on a busy month (DEV ONLY, DECISIONS C4).

Adds sample HR events (interviews, deadlines, meetings), the fixed-date Philippine national
holidays near today, and leave requests for a few leave-eligible people. Every item is tagged
"[demo]" and the previous run's tagged items are removed first, so it is safe to run before
each presentation. Runs with auditing off, like the other seeders.
"""

from datetime import date, time, timedelta
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from hr.audit import disable_audit
from hr.models import CalendarEvent, Engagement, LeaveRequest, LeaveType, Office, Person

DEMO_TAG = "[demo]"

# Fixed-date national holidays (month, day). Movable ones (Holy Week, Eid) are left out.
PH_FIXED_HOLIDAYS = [
    (1, 1, "New Year's Day"), (4, 9, "Araw ng Kagitingan"), (5, 1, "Labor Day"),
    (6, 12, "Independence Day"), (8, 21, "Ninoy Aquino Day"), (11, 1, "All Saints' Day"),
    (11, 2, "All Souls' Day"), (11, 30, "Bonifacio Day"), (12, 8, "Feast of the Immaculate Conception"),
    (12, 24, "Christmas Eve"), (12, 25, "Christmas Day"), (12, 30, "Rizal Day"),
    (12, 31, "Last Day of the Year"),
]

# (days from today, title, kind, time, office acronym or None, length in days)
SAMPLE_EVENTS = [
    (-3, "Orientation for new Job Order hires", CalendarEvent.Kind.MEETING, time(14, 0), "HRMO", 2),
    (1, "HRMO staff meeting", CalendarEvent.Kind.MEETING, time(9, 0), "HRMO", 1),
    (2, "Interview: Admin Aide I applicants", CalendarEvent.Kind.INTERVIEW, time(9, 30), "HRMO", 1),
    (3, "Interview: Revenue Clerk applicants", CalendarEvent.Kind.INTERVIEW, time(13, 30), "MTO", 1),
    (5, "CSC RAI report due", CalendarEvent.Kind.DEADLINE, None, None, 1),
    (8, "Payroll inputs cut-off", CalendarEvent.Kind.DEADLINE, None, None, 1),
    (12, "IPCR submissions due (SPMS)", CalendarEvent.Kind.DEADLINE, None, None, 1),
    (15, "Municipal foundation day (sample date)", CalendarEvent.Kind.HOLIDAY, None, None, 1),
    (18, "Gender and Development seminar", CalendarEvent.Kind.OTHER, time(8, 0), None, 2),
]

# (days from today to start, length in days, leave type, approved?)
SAMPLE_LEAVE = [
    (-2, 3, LeaveType.VACATION, True), (0, 2, LeaveType.SICK, True), (1, 1, LeaveType.SPECIAL_PRIVILEGE, False),
    (3, 5, LeaveType.VACATION, True), (4, 1, LeaveType.SICK, False), (6, 3, LeaveType.VACATION, False),
    (9, 5, LeaveType.VACATION, True), (11, 1, LeaveType.SPECIAL_PRIVILEGE, True),
]


def _working_days(start, end):
    days = sum(1 for n in range((end - start).days + 1) if (start + timedelta(days=n)).weekday() < 5)
    return Decimal(max(days, 1))


class Command(BaseCommand):
    help = "Adds sample calendar events, holidays and leave around today for a demo (DEV ONLY)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_calendar is for local demos only (DJANGO_DEBUG=1).")

        today = timezone.localdate()
        offices = {office.acronym: office for office in Office.objects.exclude(acronym="")}
        with disable_audit(), transaction.atomic():
            CalendarEvent.objects.filter(notes__startswith=DEMO_TAG).delete()
            LeaveRequest.objects.filter(reason__startswith=DEMO_TAG).delete()

            events = []
            for offset, title, kind, at, acronym, length in SAMPLE_EVENTS:
                start = today + timedelta(days=offset)
                events.append(CalendarEvent(
                    title=title, kind=kind, start_date=start, time=at, office=offices.get(acronym),
                    end_date=start + timedelta(days=length - 1) if length > 1 else None,
                    notes=f"{DEMO_TAG} Sample event for the demo.",
                ))
            for year in (today.year, today.year + 1):
                for month, day, name in PH_FIXED_HOLIDAYS:
                    holiday = date(year, month, day)
                    if today - timedelta(days=7) <= holiday <= today + timedelta(days=60):
                        events.append(CalendarEvent(
                            title=name, kind=CalendarEvent.Kind.HOLIDAY, start_date=holiday,
                            notes=f"{DEMO_TAG} Regular or special non-working day.",
                        ))
            for event in events:
                event.save()

            # Leave for leave-eligible people, two of them in the Treasurer's Office so the
            # Office Head demo account has something to approve.
            eligible = Person.objects.filter(
                engagements__status=Engagement.Status.ACTIVE, engagements__leave_eligible=True
            ).distinct().order_by("employee_no")
            mto = offices.get("MTO")
            in_mto = list(eligible.filter(engagements__office=mto)[:2]) if mto else []
            people = in_mto + [p for p in eligible[:20] if p not in in_mto]
            leaves = 0
            for person, (offset, length, leave_type, approved) in zip(people, SAMPLE_LEAVE):
                start = today + timedelta(days=offset)
                end = start + timedelta(days=length - 1)
                LeaveRequest.objects.create(
                    person=person, leave_type=leave_type, start_date=start, end_date=end,
                    days=_working_days(start, end),
                    status=LeaveRequest.Status.APPROVED if approved else LeaveRequest.Status.PENDING,
                    reason=f"{DEMO_TAG} Sample leave for the demo.",
                )
                leaves += 1

        self.stdout.write(self.style.SUCCESS(
            f"Calendar demo data: {len(events)} events and {leaves} leave requests around {today:%Y-%m-%d}."
        ))
