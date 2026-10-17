"""Required meetings tracker: periods, expected attendees and reminders (DECISIONS MT1-MT5)."""

from datetime import date, timedelta

from .models import Engagement, Meeting, MeetingSeries, Person

# The menu dot shows when the current period's meeting is not held and the period ends this soon.
ATTENTION_DAYS = 21


def period_of(day, months):
    """The calendar period of `months` length containing `day`: months, quarters, halves or years."""
    first_month = (day.month - 1) // months * months + 1
    start = date(day.year, first_month, 1)
    end_month = first_month + months
    end = date(day.year + (end_month - 1) // 12, (end_month - 1) % 12 + 1, 1) - timedelta(days=1)
    return start, end


def ensure_meetings(today, series_qs=None):
    """Creates the Meeting row for every period from each active series' start to today. Idempotent."""
    series_qs = series_qs if series_qs is not None else MeetingSeries.objects.filter(is_active=True)
    for series in series_qs:
        months = series.MONTHS[series.frequency]
        start, end = period_of(series.starts_on, months)
        existing = set(series.meetings.values_list("period_start", flat=True))
        while start <= today:
            if start not in existing:
                Meeting.objects.create(series=series, period_start=start, period_end=end)
            start, end = period_of(end + timedelta(days=1), months)


def expected_people(series):
    """Who should attend: everyone in active service, one office's active staff, or the chosen members."""
    if series.audience == MeetingSeries.Audience.CHOSEN:
        return series.members.all().order_by("last_name", "first_name")
    active = Engagement.objects.filter(status=Engagement.Status.ACTIVE)
    if series.audience == MeetingSeries.Audience.OFFICE:
        active = active.filter(office_id=series.office_id)
    return Person.objects.filter(pk__in=active.values("person_id")).order_by("last_name", "first_name")


def current_meetings(today):
    """The meeting of the current period for each active series."""
    ensure_meetings(today)
    return Meeting.objects.filter(
        series__is_active=True, period_start__lte=today, period_end__gte=today
    ).select_related("series", "series__office")


def needs_attention(today):
    """True when a current-period meeting is neither held nor excused and its period ends soon."""
    ensure_meetings(today)
    return Meeting.objects.filter(
        series__is_active=True, period_start__lte=today, period_end__gte=today,
        period_end__lte=today + timedelta(days=ATTENTION_DAYS),
        held_on__isnull=True, not_held_reason="",
    ).exists()
