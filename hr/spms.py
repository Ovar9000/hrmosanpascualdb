"""SPMS performance ratings by semester, and what other features read from them: the step
increment and loyalty checklists, and applicants (DECISIONS SP1-SP5)."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.conf import settings

from .models import Engagement, Office, PerformanceRating

# Five-point SPMS scale (SP2): the lowest final rating that earns each adjectival rating.
BANDS = [
    (Decimal("4.500"), "Outstanding"),
    (Decimal("3.500"), "Very Satisfactory"),
    (Decimal("2.500"), "Satisfactory"),
    (Decimal("1.500"), "Unsatisfactory"),
    (Decimal("1.000"), "Poor"),
]
SATISFACTORY = Decimal("2.500")


def adjectival(rating):
    if rating is None:
        return ""
    return next((label for floor, label in BANDS if rating >= floor), "Poor")


def period_bounds(year, semester):
    return (date(year, 1, 1), date(year, 6, 30)) if semester == 1 else (date(year, 7, 1), date(year, 12, 31))


def period_of(day):
    return day.year, 1 if day.month <= 6 else 2


def period_label(year, semester):
    return f"{'Jan–Jun' if semester == 1 else 'Jul–Dec'} {year}"


def last_ended(today):
    """The latest semester that is over: the one HRMO is collecting ratings for."""
    return (today.year - 1, 2) if today.month <= 6 else (today.year, 1)


def periods_between(start, end):
    """Every semester that overlaps start..end, oldest first."""
    periods, current = [], period_of(start)
    while current <= period_of(end):
        periods.append(current)
        current = (current[0], 2) if current[1] == 1 else (current[0] + 1, 1)
    return periods


def rated_engagements(office=None):
    """Active appointments rated under SPMS: JO and COS are not employees (SP1)."""
    engs = Engagement.objects.filter(status=Engagement.Status.ACTIVE, type__in=settings.SPMS_RATED_TYPES)
    return engs.filter(office=office) if office else engs


def recent_ratings(person, count=2):
    return list(PerformanceRating.objects.filter(person=person).order_by("-year", "-semester")[:count])


def coverage(person, start, end):
    """The ratings for every semester from start to end: which are missing, and whether all on
    file are at least Satisfactory."""
    wanted = periods_between(start, end) if start <= end else []
    wanted_set = set(wanted)
    found = {(r.year, r.semester): r for r in PerformanceRating.objects.filter(person=person)
             if (r.year, r.semester) in wanted_set}
    missing = [p for p in wanted if p not in found]
    rows = [{"period": p, "label": period_label(*p), "rating": found.get(p)} for p in wanted]
    return {
        "rows": rows,
        "missing": missing,
        "all_satisfactory": all(r.rating >= SATISFACTORY for r in found.values()),
        "complete": bool(wanted) and not missing,
    }


def case_coverage(case, today):
    """Ratings over a salary action's counted service, up to its due date or today (SP3)."""
    return coverage(case.person, case.service_from, min(case.due_date, today))


def prefill_checklist(case, today):
    """Ticks "ratings at least Satisfactory" when every semester of the counted service is rated and
    none is below Satisfactory. HRMO can still untick it. Returns whether it ticked."""
    cov = case_coverage(case, today)
    if cov["complete"] and cov["all_satisfactory"]:
        case.checklist = {**case.checklist, "rating": True}
        return True
    return False


def office_progress(year, semester):
    """For each office with rated staff: how many are rated for the semester (SP4)."""
    engs = list(rated_engagements().select_related("office"))
    rated = set(PerformanceRating.objects.filter(year=year, semester=semester).values_list("person_id", flat=True))
    by_office = defaultdict(set)
    for eng in engs:
        by_office[eng.office].add(eng.person_id)
    rows = []
    for office in sorted(by_office, key=lambda o: o.name):
        people = by_office[office]
        done = len(people & rated)
        rows.append({"office": office, "total": len(people), "rated": done,
                     "pct": round(100 * done / len(people)) if people else 0})
    return rows


def unrated_count(year, semester):
    rated = PerformanceRating.objects.filter(year=year, semester=semester).values("person_id")
    return rated_engagements().exclude(person_id__in=rated).values("person_id").distinct().count()


def office_sheet(office, year, semester):
    """The people of an office to rate for a semester, with any rating already entered."""
    people = {}
    for eng in rated_engagements(office).select_related("person").order_by("person__last_name", "person__first_name"):
        people.setdefault(eng.person_id, eng)
    existing = {r.person_id: r for r in PerformanceRating.objects.filter(year=year, semester=semester,
                                                                        person_id__in=people)}
    return [{"eng": eng, "person": eng.person, "rating": existing.get(pid)} for pid, eng in people.items()]


def offices_with_rated_staff():
    return Office.objects.filter(pk__in=rated_engagements().values("office_id")).order_by("name")
