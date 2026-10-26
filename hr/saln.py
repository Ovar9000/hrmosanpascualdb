"""SALN filing tracker: who must file for a year, who said they filed, and who HRMO has received
from, against the April 30 deadline (DECISIONS SN1-SN4)."""

from datetime import date

from django.conf import settings

from .models import Engagement, Person, SalnFiling

REMIND_DAYS = 60  # employees see the reminder this many days before the deadline


def deadline(year):
    """The SALN for a year is due April 30 of the next year (RA 6713)."""
    return date(year + 1, 4, 30)


def current_year(today):
    """The year being filed now: last year's SALN, due this April 30."""
    return today.year - 1


def filing_year(today):
    """The year the tracker and the employee page show: the current one, or the first tracked year
    while that has not come round yet (SN4)."""
    return max(current_year(today), settings.SALN_FIRST_YEAR)


def is_open(year, today):
    """A SALN is as of December 31, so it can be filed from January 1 of the next year."""
    return today >= date(year + 1, 1, 1)


def tracked(year):
    return year >= settings.SALN_FIRST_YEAR


def covered_engagements(office=None):
    engs = Engagement.objects.filter(status=Engagement.Status.ACTIVE, type__in=settings.SALN_COVERED_TYPES)
    return engs.filter(office=office) if office else engs


def is_covered(person):
    return covered_engagements().filter(person=person).exists()


def rows(year, office=None):
    """Everyone who must file for the year, with their filing, by office then name."""
    current = {}
    for eng in covered_engagements(office).select_related("person", "office").order_by("-start_date"):
        current.setdefault(eng.person_id, eng)
    filings = {f.person_id: f for f in SalnFiling.objects.filter(year=year, person_id__in=current)}
    out = []
    for pid, eng in current.items():
        filing = filings.get(pid)
        state = "received" if filing and filing.received_on else "reported" if filing and filing.reported_at else "missing"
        out.append({"person": eng.person, "office": eng.office, "filing": filing, "state": state,
                    "late": bool(filing and filing.received_on and filing.received_on > deadline(year))})
    out.sort(key=lambda r: (r["office"].name, r["person"].last_name, r["person"].first_name))
    return out


def my_reminder(person, today):
    """(year, filing, days to deadline) when the person should file and HRMO has not received it."""
    year = current_year(today)
    due = deadline(year)
    if not tracked(year) or (due - today).days > REMIND_DAYS or not is_covered(person):
        return None
    filing = SalnFiling.objects.filter(person=person, year=year).first()
    if filing and filing.received_on:
        return None
    return {"year": year, "filing": filing, "days": (due - today).days, "deadline": due}


def missing_count(year):
    received = SalnFiling.objects.filter(year=year, received_on__isnull=False).values("person_id")
    return covered_engagements().exclude(person_id__in=received).values("person_id").distinct().count()


def covered_people(ids):
    """The people among ids who must file, for HRMO's "mark received"."""
    return Person.objects.filter(pk__in=ids, engagements__in=covered_engagements()).distinct()
