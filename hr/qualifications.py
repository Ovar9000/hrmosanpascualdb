"""Licences, eligibility, learning and development hours, and the evidence for an applicant's
qualification standards, all read from the Personal Data Sheet (DECISIONS EL1-EL3, LD1-LD3)."""

import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Q

from .models import Engagement, PdsEducation, PdsEligibility, PdsSubmission, PdsTraining, PdsWorkExperience, Person
from .salary_actions import add_years

EDUCATION_ORDER = ["elementary", "secondary", "vocational", "college", "graduate"]


def _current_offices(person_ids):
    """Each person's office in their latest active appointment."""
    offices = {}
    for eng in (Engagement.objects.filter(person_id__in=person_ids, status=Engagement.Status.ACTIVE)
                .select_related("office").order_by("person_id", "-start_date")):
        offices.setdefault(eng.person_id, eng.office)
    return offices


def _verified(person_ids):
    return set(PdsSubmission.objects.filter(person_id__in=person_ids, status=PdsSubmission.Status.VERIFIED)
               .values_list("person_id", flat=True))


def active_people(office=None):
    active = Engagement.objects.filter(status=Engagement.Status.ACTIVE)
    if office:
        active = active.filter(office=office)
    return Person.objects.filter(pk__in=active.values("person_id"))


# --- Licences and eligibility (EL1, EL2) --------------------------------------------------


def licence_rows(today, office=None, show="attention", query=""):
    """Eligibility and licence rows of people in active service. "attention" lists licences that
    expired or expire within LICENCE_LOOKAHEAD_DAYS; a search lists every matching eligibility."""
    window = today + timedelta(days=settings.LICENCE_LOOKAHEAD_DAYS)
    rows = PdsEligibility.objects.filter(person__in=active_people(office)).select_related("person")
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(license_number__icontains=query))
    elif show == "attention":
        rows = rows.filter(license_valid_until__isnull=False, license_valid_until__lte=window)
    rows = list(rows.order_by("license_valid_until", "person__last_name"))
    ids = {r.person_id for r in rows}
    offices, verified = _current_offices(ids), _verified(ids)
    out = []
    for r in rows:
        until = r.license_valid_until
        state = "none" if until is None else "expired" if until < today else "soon" if until <= window else "valid"
        out.append({"elig": r, "person": r.person, "office": offices.get(r.person_id), "state": state,
                    "days": (until - today).days if until else None,
                    "ago": (today - until).days if until else None, "verified": r.person_id in verified})
    return out


def licence_attention_count(today):
    window = today + timedelta(days=settings.LICENCE_LOOKAHEAD_DAYS)
    return PdsEligibility.objects.filter(person__in=active_people(), license_valid_until__isnull=False,
                                         license_valid_until__lte=window).count()


def my_licences_due(person, today):
    """The person's own licences that expired or expire soon, for their home page."""
    window = today + timedelta(days=settings.LICENCE_LOOKAHEAD_DAYS)
    return list(PdsEligibility.objects.filter(person=person, license_valid_until__isnull=False,
                                              license_valid_until__lte=window).order_by("license_valid_until"))


# --- Learning and development (LD1, LD3) ---------------------------------------------------


def hours_by_person(person_ids, year):
    totals = defaultdict(lambda: {"hours": 0, "count": 0, "last": None})
    for t in PdsTraining.objects.filter(person_id__in=person_ids, date_from__year=year):
        row = totals[t.person_id]
        row["hours"] += t.hours or 0
        row["count"] += 1
        row["last"] = max(row["last"], t.date_from) if row["last"] else t.date_from
    return totals


def learning_rows(year, office=None):
    people = list(active_people(office).order_by("last_name", "first_name"))
    ids = [p.pk for p in people]
    totals, offices = hours_by_person(ids, year), _current_offices(ids)
    target = settings.LD_TARGET_HOURS
    rows = []
    for p in people:
        t = totals.get(p.pk, {"hours": 0, "count": 0, "last": None})
        rows.append({"person": p, "office": offices.get(p.pk), **t,
                     "short": (t["hours"] < target) if target else t["hours"] == 0})
    return rows


def my_hours(person, year):
    return hours_by_person([person.pk], year).get(person.pk, {"hours": 0, "count": 0, "last": None})


# --- An applicant's qualifications (EL3) ---------------------------------------------------

_WORDS = re.compile(r"[a-z0-9]+")
_COMMON = {"the", "of", "and", "or", "for", "to", "in", "a", "an", "relevant", "eligibility", "required", "none",
           "service", "rating", "level", "first", "second"}


def _keywords(text):
    return {w for w in _WORDS.findall((text or "").lower()) if len(w) > 2 and w not in _COMMON}


def eligibility_match(vacancy_text, names):
    """Eligibilities whose distinctive words all appear in the vacancy's eligibility requirement,
    e.g. "Career Service Professional" in "Career Service (Professional) / Second Level". A hint for
    HRMO, never a decision."""
    wanted = _keywords(vacancy_text)
    return [n for n in names if _keywords(n) and _keywords(n) <= wanted]


def experience_years(person, today):
    """Years of work on the PDS plus appointments here, overlaps not double-counted."""
    spans = [(e.start_date, e.end_date or today) for e in person.engagements.all()]
    spans += [(w.date_from, w.date_to or today) for w in PdsWorkExperience.objects.filter(person=person)]
    total, current = 0, None
    for start, end in sorted(spans):
        if current and start <= current[1]:
            current = (current[0], max(current[1], end))
            continue
        if current:
            total += (current[1] - current[0]).days
        current = (start, end)
    if current:
        total += (current[1] - current[0]).days
    return Decimal(total / 365.25).quantize(Decimal("0.1"))


def applicant_evidence(applicant, vacancy, today):
    """What the records say about an employee-applicant against the qualification standards."""
    person = applicant.person
    if person is None:
        return None
    from .spms import recent_ratings

    education = sorted(PdsEducation.objects.filter(person=person),
                       key=lambda e: EDUCATION_ORDER.index(e.level) if e.level in EDUCATION_ORDER else -1)
    names = list(PdsEligibility.objects.filter(person=person).values_list("name", flat=True))
    five_years_ago = add_years(today, -5)
    hours = sum(t.hours or 0 for t in PdsTraining.objects.filter(person=person, date_from__gte=five_years_ago))
    return {
        "education": education[-1] if education else None,
        "eligibilities": names,
        "matches": eligibility_match(vacancy.eligibility, names),
        "training_hours": hours,
        "experience": experience_years(person, today),
        "ratings": recent_ratings(person, 2),
        "verified": PdsSubmission.objects.filter(person=person, status=PdsSubmission.Status.VERIFIED).exists(),
    }
