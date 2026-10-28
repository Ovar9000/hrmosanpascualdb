"""Plantilla of Personnel report, vacancies and memo audiences (DECISIONS RP1, V1-V5, M1-M4)."""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q

from .models import Engagement, Memo, MemoAcknowledgment, Office, Person, PlantillaItem, Vacancy

# Vacancies whose item still counts as vacant on the Plantilla.
VACANT_STATUSES = [Vacancy.Status.DRAFT, Vacancy.Status.OPEN, Vacancy.Status.SCREENING]


def annual_salary(eng):
    """Actual annual salary from the stored rate (D2: an input, not computed pay)."""
    if eng.rate is None:
        return None
    if eng.rate_basis == Engagement.RateBasis.MONTHLY:
        return eng.rate * 12
    if eng.rate_basis == Engagement.RateBasis.LUMP_SUM:
        return eng.rate
    return None


def _career(person_ids):
    """Original appointment and last promotion dates per person, from their plantilla history."""
    history = defaultdict(list)
    for eng in (Engagement.objects.filter(person_id__in=person_ids, type=Engagement.Type.PLANTILLA)
                .order_by("start_date").only("person_id", "start_date", "salary_grade")):
        history[eng.person_id].append(eng)
    result = {}
    for person_id, engs in history.items():
        promoted = None
        for before, after in zip(engs, engs[1:]):
            if (after.salary_grade or 0) > (before.salary_grade or 0):
                promoted = after.start_date
        result[person_id] = (engs[0].start_date, promoted)
    return result


def _vacant_annual(item, vacancy, today):
    """A vacant item's annual salary: from its vacancy notice, else step 1 of its grade in the salary
    schedule in force (NS1), else unknown."""
    if vacancy is not None:
        return vacancy.monthly_salary * 12
    from .papers import rate_in, schedule_on

    monthly = rate_in(schedule_on(today), item.salary_grade, 1)
    return monthly * 12 if monthly is not None else None


def plantilla_rows(office=None, today=None):
    """[(office, rows, totals)] in office order: every plantilla item that is not abolished, filled by its
    active appointment or vacant (with its vacancy notice, if any), plus vacancy notices for item numbers
    not on file yet (RP1, PI3)."""
    from django.utils import timezone

    today = today or timezone.localdate()
    items = PlantillaItem.objects.exclude(status=PlantillaItem.Status.ABOLISHED).select_related("office")
    filled = (Engagement.objects.filter(type=Engagement.Type.PLANTILLA, status=Engagement.Status.ACTIVE)
              .select_related("person", "office"))
    vacant = Vacancy.objects.filter(status__in=VACANT_STATUSES).select_related("office").order_by("created_at")
    if office:
        items, filled, vacant = items.filter(office=office), filled.filter(office=office), vacant.filter(office=office)
    career = _career({e.person_id for e in filled})
    holders = {e.plantilla_item_id: e for e in filled if e.plantilla_item_id}
    notices = {v.item_number.strip().lower(): v for v in vacant}
    by_office = defaultdict(list)
    seen = set()

    def filled_row(eng):
        original, promoted = career.get(eng.person_id, (eng.start_date, None))
        return {
            "item": eng.item_number, "position": eng.position_title, "sg": eng.salary_grade, "step": eng.step,
            "annual": annual_salary(eng), "person": eng.person, "vacant": False,
            "original": original, "promoted": promoted, "status": eng.appointment_status,
            "status_label": eng.get_appointment_status_display(), "eng": eng,
        }

    for item in items:
        key = item.item_number.strip().lower()
        seen.add(key)
        eng = holders.get(item.pk)
        if eng is not None:
            row = filled_row(eng)
            by_office[eng.office].append(row)
        else:
            vac = notices.get(key)
            row = {"item": item.item_number, "position": item.position_title, "sg": item.salary_grade, "step": 1,
                   "annual": _vacant_annual(item, vac, today), "person": None, "vacant": True, "vacancy": vac}
            by_office[item.office].append(row)
        row["unfunded"] = item.status == PlantillaItem.Status.UNFUNDED
        row["plantilla_item"] = item
    # Appointments whose item is filed under another office, or not linked to an item.
    listed = {i.pk for i in items}
    for eng in filled:
        key = eng.item_number.strip().lower()
        if eng.plantilla_item_id not in listed and key not in seen:
            seen.add(key)
            by_office[eng.office].append(filled_row(eng))
    for key, vac in notices.items():
        if key not in seen:
            by_office[vac.office].append({
                "item": vac.item_number, "position": vac.position_title, "sg": vac.salary_grade, "step": 1,
                "annual": vac.monthly_salary * 12, "person": None, "vacant": True, "vacancy": vac,
            })
    groups = []
    for off in sorted(by_office, key=lambda o: o.name):
        rows = sorted(by_office[off], key=lambda r: r["item"])
        groups.append((off, rows, {
            "filled": sum(1 for r in rows if not r["vacant"]),
            "vacant": sum(1 for r in rows if r["vacant"]),
            "annual": sum((r["annual"] or Decimal("0")) for r in rows),
        }))
    return groups


# --- Memos -----------------------------------------------------------------------------


def memo_people(memo):
    """Who the memo is addressed to: everyone in active service, one office's staff, or the chosen."""
    if memo.audience == Memo.Audience.CHOSEN:
        return memo.members.all().order_by("last_name", "first_name")
    active = Engagement.objects.filter(status=Engagement.Status.ACTIVE)
    if memo.audience == Memo.Audience.OFFICE:
        active = active.filter(office_id=memo.office_id)
    return Person.objects.filter(pk__in=active.values("person_id")).order_by("last_name", "first_name")


def memos_for_person(person):
    """Published memos addressed to this person, newest first."""
    if person is None:
        return Memo.objects.none()
    active = person.engagements.filter(status=Engagement.Status.ACTIVE)
    offices = list(active.values_list("office_id", flat=True))
    memos = Memo.objects.filter(is_published=True)
    if not offices:
        return memos.filter(audience=Memo.Audience.CHOSEN, members=person)

    return memos.filter(
        Q(audience=Memo.Audience.EVERYONE)
        | Q(audience=Memo.Audience.OFFICE, office_id__in=offices)
        | Q(audience=Memo.Audience.CHOSEN, members=person)
    ).distinct()


def unread_memos(person):
    """Memos addressed to the person that ask for an acknowledgment they have not given."""
    if person is None:
        return Memo.objects.none()
    done = MemoAcknowledgment.objects.filter(person=person).values("memo_id")
    return memos_for_person(person).filter(requires_ack=True).exclude(pk__in=done)


def ack_rows(memo, office=None):
    """Each addressee with when they acknowledged, grouped by office for the routing view (M3)."""
    people = memo_people(memo)
    if office:
        people = people.filter(engagements__office=office, engagements__status=Engagement.Status.ACTIVE).distinct()
    acks = {a.person_id: a for a in memo.acknowledgments.all()}
    current = {}
    for eng in (Engagement.objects.filter(person__in=people, status=Engagement.Status.ACTIVE)
                .select_related("office").order_by("-start_date")):
        current.setdefault(eng.person_id, eng)
    rows = []
    for person in people:
        eng = current.get(person.pk)
        rows.append({"person": person, "office": eng.office if eng else None, "eng": eng, "ack": acks.get(person.pk)})
    rows.sort(key=lambda r: ((r["office"].name if r["office"] else "~"), r["person"].last_name, r["person"].first_name))
    return rows


# --- HRMPSB resolution -------------------------------------------------------------------


def resolution_text(vacancy):
    """A draft of the HRMPSB resolution from the vacancy and its applicants (V4). HRMO edits the
    printed copy as needed; the signed paper is the record (F1)."""
    qualified = [a for a in vacancy.applicants.all() if a.meets_qs]

    def joined(items):
        return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)

    return {
        "named": joined([a.display_name for a in qualified]) or "[no qualified applicant recorded]",
        "signified": joined([f"{a.signified_on:%B %d, %Y}" for a in qualified]),
        "qualified": qualified,
        "respectively": len(qualified) > 1,
    }


def offices_with_vacancies():
    return Office.objects.filter(vacancies__status__in=Vacancy.VISIBLE_STATUSES).distinct().order_by("name")
