"""Reminder lists computed from the records on every visit: contract renewals, retirement and
year-end bonus eligibility (DECISIONS RM1-RM5).

Nothing here is stored or changes a record. Each function returns rows for a page and an Excel
export; HRMO acts through the existing screens (renew, end appointment). Rules marked
unconfirmed in DECISIONS are kept as module constants so they are easy to change.
"""

from datetime import date, timedelta

from django.conf import settings
from django.db.models import F, Q

from .models import Engagement, Person
from .salary_actions import add_years, service_stretches

# --- Contract renewals (RM1) ----------------------------------------------------------


def renewal_rows(today, office=None, eng_type=""):
    """Active appointments past their end date ("lapsed") or ending within the window.

    An upcoming one drops off once the same person has a later appointment (renewed). A lapsed
    one stays until it is ended, flagged when a renewal already exists.
    """
    horizon = today + timedelta(days=settings.RENEWAL_LOOKAHEAD_DAYS)
    qs = Engagement.objects.filter(status=Engagement.Status.ACTIVE, end_date__isnull=False, end_date__lte=horizon)
    if office:
        qs = qs.filter(office=office)
    if eng_type:
        qs = qs.filter(type=eng_type)
    renewed = set(
        qs.filter(person__engagements__start_date__gt=F("start_date")).values_list("pk", flat=True)
    )
    rows = []
    for eng in qs.select_related("person", "office", "project").order_by("end_date", "office__name", "person__last_name"):
        days = (eng.end_date - today).days
        if days >= 0 and eng.pk in renewed:
            continue
        rows.append({"eng": eng, "lapsed": days < 0, "days": days, "ago": -days, "renewed": eng.pk in renewed})
    return rows


# --- Retirement (RM2) ---------------------------------------------------------------

COMPULSORY_AGE, OPTIONAL_AGE, OPTIONAL_SERVICE_YEARS = 65, 60, 15  # RA 8291 Sec. 13, 13-A
# GSIS members: appointive government personnel. JO, COS and project-based workers are not
# members; elected officials have no compulsory retirement age.
GSIS_TYPES = [Engagement.Type.PLANTILLA, Engagement.Type.CASUAL, Engagement.Type.CONTRACTUAL]
RETIREMENT_WINDOW_DAYS = 365


def _age(birthdate, on):
    return on.year - birthdate.year - ((on.month, on.day) < (birthdate.month, birthdate.day))


def retirement_rows(today):
    """Three groups: past 65 and still active, turning 65 within a year, and able to retire
    optionally now (60 or older with 15 years of unbroken service recorded here)."""
    people = (
        Person.objects.filter(engagements__status=Engagement.Status.ACTIVE, engagements__type__in=GSIS_TYPES)
        .distinct()
        .prefetch_related("engagements__office")
    )
    past, soon, optional = [], [], []
    for person in people:
        engagements = list(person.engagements.all())
        current = max(
            (e for e in engagements if e.status == Engagement.Status.ACTIVE and e.type in GSIS_TYPES),
            key=lambda e: e.start_date,
        )
        compulsory = add_years(person.birthdate, COMPULSORY_AGE)
        stretches = service_stretches(engagements, today)
        service_from = stretches[-1][0] if stretches else current.start_date
        years = _age(service_from, today)  # whole years of unbroken service
        row = {
            "person": person, "eng": current, "age": _age(person.birthdate, today),
            "compulsory": compulsory, "service_from": service_from, "service_years": years,
            "days": (compulsory - today).days,
        }
        if compulsory <= today:
            past.append(row)
        elif compulsory <= today + timedelta(days=RETIREMENT_WINDOW_DAYS):
            soon.append(row)
        elif row["age"] >= OPTIONAL_AGE and years >= OPTIONAL_SERVICE_YEARS:
            optional.append(row)
    key = lambda r: (r["compulsory"], r["person"].last_name)  # noqa: E731
    return {"past": sorted(past, key=key), "soon": sorted(soon, key=key), "optional": sorted(optional, key=key)}


# --- Year-end bonus and cash gift (RM3) ---------------------------------------------

# RA 6686 as amended by RA 8441; DBM budget circulars: civilian personnel, appointive or
# elective. JO, COS and project-based workers are not government employees (unconfirmed for
# project-based).
YEB_TYPES = [Engagement.Type.ELECTED, Engagement.Type.PLANTILLA, Engagement.Type.CASUAL, Engagement.Type.CONTRACTUAL]
YEB_MIN_DAYS = 120  # "at least four months" from January 1 to October 31, counted as 120 days
ELIGIBLE, PRORATED, NOT_IN_SERVICE = "eligible", "prorated", "not_in_service"
YEB_LABELS = {
    ELIGIBLE: "Year-end bonus and full cash gift",
    PRORATED: "Cash gift only, prorated (under 4 months)",
    NOT_IN_SERVICE: "Not eligible: not in service on October 31",
}


def _merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def yeb_rows(year, today):
    """Everyone with covered service between January 1 and October 31 of `year`.

    Before October 31 the list is a projection: current appointments are assumed to continue
    to their end date (or to October 31 if open-ended).
    """
    first, cutoff = date(year, 1, 1), date(year, 10, 31)
    engs = (
        Engagement.objects.filter(type__in=YEB_TYPES, start_date__lte=cutoff)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=first))
        .exclude(~Q(status=Engagement.Status.ACTIVE) & Q(end_date__isnull=True))  # ended without a date
        .select_related("person", "office")
    )
    by_person = {}
    for eng in engs:
        by_person.setdefault(eng.person_id, []).append(eng)
    rows = []
    for person_engs in by_person.values():
        intervals = [(max(e.start_date, first), min(e.end_date or cutoff, cutoff)) for e in person_engs]
        days = sum((end - start).days + 1 for start, end in _merge(intervals) if end >= start)
        on_cutoff = [e for e in person_engs if e.start_date <= cutoff and (e.end_date is None or e.end_date >= cutoff)]
        if not on_cutoff:
            result = NOT_IN_SERVICE
        elif days >= YEB_MIN_DAYS:
            result = ELIGIBLE
        else:
            result = PRORATED
        latest = max(on_cutoff or person_engs, key=lambda e: e.start_date)
        rows.append({
            "person": latest.person, "eng": latest, "days": days, "result": result,
            "label": YEB_LABELS[result], "in_service": bool(on_cutoff),
        })
    order = {ELIGIBLE: 0, PRORATED: 1, NOT_IN_SERVICE: 2}
    rows.sort(key=lambda r: (order[r["result"]], r["eng"].office.name, r["person"].last_name))
    return rows


# --- Menu dot ------------------------------------------------------------------------


def needs_attention(today):
    """True when a renewal has lapsed or someone active is past compulsory retirement age."""
    if Engagement.objects.filter(status=Engagement.Status.ACTIVE, end_date__lt=today).exists():
        return True
    return Person.objects.filter(
        birthdate__lte=add_years(today, -COMPULSORY_AGE),
        engagements__status=Engagement.Status.ACTIVE,
        engagements__type__in=GSIS_TYPES,
    ).exists()
