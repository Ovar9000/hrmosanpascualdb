"""Licences, learning and development, SPMS ratings, SALN filing and overtime credits (DECISIONS
EL1-EL3, LD1-LD3, SP1-SP5, SN1-SN4, CT1-CT5)."""

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import coc, qualifications, saln, spms
from .models import CocEntry, Engagement, Office, PdsTraining, PerformanceRating, Person, SalnFiling
from .permissions import can_manage_hr_records, can_view_leave_of, is_self, is_sysadmin, saln_scope
from .views import _require_tracker_view, _xlsx_response


def _office(request):
    value = request.GET.get("office", "")
    return Office.objects.filter(pk=int(value)).first() if value.isdigit() else None


def _int(value, default, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def _require_manage(request):
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("Only HRMO records this.")


def _own_person(user):
    profile = getattr(user, "profile", None)
    return profile.person if profile and profile.person_id else None


def _not_self(user, people):
    """HRMO never credits their own record (R10): drop the user's own person from a picker's people."""
    own = _own_person(user)
    return people.exclude(pk=own.pk) if own else people


def _picker_people(user, office=None, exclude_types=()):
    """People in active service, with their office, for the "who attended" pickers. The user's own
    record is left out (R10)."""
    engs = Engagement.objects.filter(status=Engagement.Status.ACTIVE).exclude(type__in=exclude_types)
    own = _own_person(user)
    if own:
        engs = engs.exclude(person=own)
    if office:
        engs = engs.filter(office=office)
    rows = {}
    for eng in engs.select_related("person", "office").order_by("office__name", "person__last_name", "person__first_name"):
        rows.setdefault(eng.person_id, eng)
    return list(rows.values())


# --- Licences and eligibility (EL1, EL2) --------------------------------------------------


@login_required
def tracker_licences(request):
    _require_tracker_view(request)
    today = timezone.localdate()
    office = _office(request)
    query = request.GET.get("q", "").strip()[:100]
    show = "all" if request.GET.get("show") == "all" else "attention"
    rows = qualifications.licence_rows(today, office, show, query)
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"licences-eligibility-{today:%Y-%m-%d}.xlsx", "Licences and eligibility",
            ["Employee no.", "Name", "Office", "Eligibility", "Rating", "License no.", "Valid until", "Status",
             "PDS verified"],
            [[r["person"].employee_no, r["person"].full_name, r["office"].name if r["office"] else "",
              r["elig"].name, r["elig"].rating, r["elig"].license_number, r["elig"].license_valid_until,
              r["state"], "Yes" if r["verified"] else "No"] for r in rows],
            "Licences and eligibility list (Excel)", model_name="PdsEligibility",
        )
    params = [(k, v) for k, v in (("office", office.pk if office else ""), ("q", query),
                                  ("show", "all" if show == "all" else "")) if v]
    return render(request, "hr/tracker_licences.html", {
        "tracker": "licences", "rows": rows, "office": office, "offices": Office.objects.order_by("name"),
        "query": query, "show": show, "window": settings.LICENCE_LOOKAHEAD_DAYS,
        "expired": sum(1 for r in rows if r["state"] == "expired"),
        "soon": sum(1 for r in rows if r["state"] == "soon"),
        "export_qs": urlencode(params + [("export", "xlsx")]),
    })


# --- Learning and development (LD1-LD3) ---------------------------------------------------


@login_required
def tracker_learning(request):
    _require_tracker_view(request)
    today = timezone.localdate()
    year = _int(request.GET.get("year"), today.year, 2000, today.year + 1)
    office = _office(request)
    rows = qualifications.learning_rows(year, office)
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"learning-hours-{year}.xlsx", f"L&D hours {year}",
            ["Employee no.", "Name", "Office", "Trainings", "Hours", "Last training"],
            [[r["person"].employee_no, r["person"].full_name, r["office"].name if r["office"] else "",
              r["count"], r["hours"], r["last"]] for r in rows],
            f"Learning and development hours {year} (Excel)", model_name="PdsTraining",
        )
    params = [(k, v) for k, v in (("office", office.pk if office else ""), ("year", year)) if v]
    total = sum(r["hours"] for r in rows)
    return render(request, "hr/tracker_learning.html", {
        "tracker": "learning", "rows": rows, "year": year, "years": range(today.year, today.year - 6, -1),
        "office": office, "offices": Office.objects.order_by("name"), "target": settings.LD_TARGET_HOURS,
        "short": sum(1 for r in rows if r["short"]), "total": total,
        "average": round(total / len(rows), 1) if rows else 0,
        "export_qs": urlencode(params + [("export", "xlsx")]),
        "can_manage": can_manage_hr_records(request.user),
    })


@login_required
def learning_record(request):
    """One training, recorded for everyone who attended (LD2)."""
    _require_tracker_view(request)
    _require_manage(request)
    errors, values = {}, {}
    if request.method == "POST":
        values = {k: request.POST.get(k, "").strip() for k in ("title", "date_from", "date_to", "hours", "kind", "sponsor")}
        try:
            start = date.fromisoformat(values["date_from"])
        except ValueError:
            errors["date_from"] = "Give the first day."
            start = None
        end = None
        if values["date_to"]:
            try:
                end = date.fromisoformat(values["date_to"])
            except ValueError:
                errors["date_to"] = "Not a date."
        if start and end and end < start:
            errors["date_to"] = "Cannot be before the first day."
        hours = _int(values["hours"], None, 1, 2000) if values["hours"] else None
        if values["hours"] and hours is None:
            errors["hours"] = "Whole hours, 1 to 2000."
        if not values["title"]:
            errors["title"] = "Give the title of the training."
        if values["kind"] not in PdsTraining.Kind.values:
            values["kind"] = ""
        ids = [int(i) for i in request.POST.getlist("person") if i.isdigit()]
        people = list(_not_self(request.user, Person.objects.filter(
            pk__in=ids, engagements__status=Engagement.Status.ACTIVE).distinct()))
        if not people:
            errors["people"] = "Tick everyone who attended."
        if not errors:
            with transaction.atomic():
                for person in people:
                    PdsTraining.objects.create(
                        person=person, title=values["title"][:255], date_from=start, date_to=end, hours=hours,
                        kind=values["kind"], sponsor=values["sponsor"][:255], by_hrmo=True,
                    )
            messages.success(request, f"Recorded \"{values['title']}\" for {len(people)} "
                                      f"{'person' if len(people) == 1 else 'people'}.")
            return redirect("tracker_learning")
    return render(request, "hr/learning_record.html", {
        "tracker": "learning", "errors": errors, "values": values, "kinds": PdsTraining.Kind.choices,
        "people": _picker_people(request.user), "ticked": set(request.POST.getlist("person")),
    })


# --- SPMS ratings (SP1-SP5) -----------------------------------------------------------------


def _period(request, today):
    year, semester = spms.last_ended(today)
    year = _int(request.GET.get("year"), year, 2000, today.year)
    semester = _int(request.GET.get("semester"), semester, 1, 2)
    return year, semester


@login_required
def tracker_ratings(request):
    _require_tracker_view(request)
    today = timezone.localdate()
    year, semester = _period(request, today)
    if request.GET.get("export") == "xlsx":
        ratings = (PerformanceRating.objects.filter(year=year, semester=semester)
                   .select_related("person", "office").order_by("office__name", "person__last_name"))
        return _xlsx_response(
            request, f"ipcr-ratings-{year}-{semester}.xlsx", f"Ratings {year}-{semester}",
            ["Employee no.", "Name", "Office", "Final rating", "Adjectival", "Notes"],
            [[r.person.employee_no, r.person.full_name, r.office.name if r.office else "", r.rating, r.adjectival,
              r.notes] for r in ratings],
            f"SPMS ratings {spms.period_label(year, semester)} (Excel)", model_name="PerformanceRating",
        )
    rows = spms.office_progress(year, semester)
    periods = []
    current = spms.last_ended(today)
    for _ in range(6):
        periods.append((current, spms.period_label(*current)))
        current = (current[0], 1) if current[1] == 2 else (current[0] - 1, 2)
    return render(request, "hr/tracker_ratings.html", {
        "tracker": "ratings", "rows": rows, "year": year, "semester": semester,
        "label": spms.period_label(year, semester), "periods": periods,
        "total": sum(r["total"] for r in rows), "rated": sum(r["rated"] for r in rows),
        "bands": spms.BANDS, "can_manage": can_manage_hr_records(request.user),
    })


@login_required
def ratings_office(request, office_pk):
    """HRMO enters an office's IPCR ratings for a semester in one sheet (SP4)."""
    _require_tracker_view(request)
    office = get_object_or_404(Office, pk=office_pk)
    today = timezone.localdate()
    year, semester = _period(request, today)
    sheet = spms.office_sheet(office, year, semester)
    can_manage = can_manage_hr_records(request.user)
    errors = {}
    if request.method == "POST":
        _require_manage(request)
        changes = []
        for row in sheet:
            pid = row["person"].pk
            if is_self(request.user, row["person"]):
                continue  # another HRMO staff member enters one's own rating (R10)
            raw = request.POST.get(f"rating-{pid}", "").strip()
            note = request.POST.get(f"note-{pid}", "").strip()[:255]
            if not raw:
                changes.append((row, None, note))
                continue
            try:
                value = Decimal(raw).quantize(Decimal("0.001"))
            except InvalidOperation:
                value = None
            if value is None or not Decimal("1") <= value <= Decimal("5"):
                errors[pid] = "1.000 to 5.000"
                continue
            changes.append((row, value, note))
        if not errors:
            saved = removed = 0
            with transaction.atomic():
                for row, value, note in changes:
                    existing = row["rating"]
                    if value is None:
                        if existing:
                            existing.delete()
                            removed += 1
                        continue
                    if existing and existing.rating == value and existing.notes == note:
                        continue
                    PerformanceRating.objects.update_or_create(
                        person=row["person"], year=year, semester=semester,
                        defaults={"rating": value, "notes": note, "office": office, "created_by": request.user},
                    )
                    saved += 1
            messages.success(request, f"Saved {saved} rating{'' if saved == 1 else 's'}"
                                      f"{f', removed {removed}' if removed else ''} for {spms.period_label(year, semester)}.")
            return redirect(f"{request.path}?year={year}&semester={semester}")
    for row in sheet:
        pid, existing = row["person"].pk, row["rating"]
        if request.method == "POST":
            row["value"] = request.POST.get(f"rating-{pid}", "")
            row["note"] = request.POST.get(f"note-{pid}", "")
        else:
            row["value"] = existing.rating if existing else ""
            row["note"] = existing.notes if existing else ""
        row["error"] = errors.get(pid)
        row["own"] = is_self(request.user, row["person"])
    return render(request, "hr/ratings_office.html", {
        "tracker": "ratings", "office": office, "sheet": sheet, "year": year, "semester": semester,
        "label": spms.period_label(year, semester), "errors": errors,
        "can_manage": can_manage, "bands": spms.BANDS,
    })


# --- SALN filing (SN1-SN4) ----------------------------------------------------------------


@login_required
def tracker_saln(request):
    scope = saln_scope(request.user)
    if scope is False:
        raise PermissionDenied("SALN filing is tracked by HRMO.")
    today = timezone.localdate()
    default = saln.filing_year(today)
    year = _int(request.GET.get("year"), default, 2000, default)
    office = scope if scope is not None else _office(request)
    can_manage = scope is None and can_manage_hr_records(request.user)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Only HRMO records SALN receipts.")
        ids = [int(i) for i in request.POST.getlist("person") if i.isdigit()]
        try:
            received_on = date.fromisoformat(request.POST.get("received_on", ""))
        except ValueError:
            received_on = today
        count = 0
        with transaction.atomic():
            for person in saln.covered_people(ids):
                if is_self(request.user, person):
                    continue  # another HRMO staff member receives one's own SALN (R10)
                filing, _ = SalnFiling.objects.get_or_create(person=person, year=year)
                if not filing.received_on:
                    filing.received_on, filing.received_by = received_on, request.user
                    filing.save()
                    count += 1
        messages.success(request, f"Marked {count} SALN{'' if count == 1 else 's'} for {year} received on {received_on:%b %d, %Y}.")
        return redirect(f"{request.path}?year={year}" + (f"&office={office.pk}" if office else ""))
    rows = saln.rows(year, office)
    show = request.GET.get("show", "")
    if show == "missing":
        rows = [r for r in rows if r["state"] != "received"]
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"saln-{year}.xlsx", f"SALN {year}",
            ["Employee no.", "Name", "Office", "Status", "Reported by employee", "Received by HRMO", "Late"],
            [[r["person"].employee_no, r["person"].full_name, r["office"].name, r["state"],
              timezone.localtime(r["filing"].reported_at).date() if r["filing"] and r["filing"].reported_at else None,
              r["filing"].received_on if r["filing"] else None, "Yes" if r["late"] else ""] for r in rows],
            f"SALN filing {year} (Excel)", model_name="SalnFiling",
        )
    all_rows = saln.rows(year, office) if show == "missing" else rows
    params = [(k, v) for k, v in (("year", year), ("office", office.pk if office and scope is None else ""),
                                  ("show", show)) if v]
    return render(request, "hr/tracker_saln.html", {
        "tracker": "saln", "rows": rows, "year": year, "years": range(default, default - 5, -1),
        "open": saln.is_open(year, today),
        "deadline": saln.deadline(year), "today": today, "office": office, "scoped": scope is not None,
        "offices": Office.objects.order_by("name"), "show": show, "can_manage": can_manage,
        "own_pk": getattr(_own_person(request.user), "pk", None),
        "counts": {s: sum(1 for r in all_rows if r["state"] == s) for s in ("received", "reported", "missing")},
        "total": len(all_rows), "export_qs": urlencode(params + [("export", "xlsx")]),
    })


@login_required
def saln_mine(request):
    """The employee's SALN filings and "I have submitted it to HRMO" (SN2)."""
    person = _own_person(request.user)
    if person is None:
        if saln_scope(request.user) is not False:
            return redirect("tracker_saln")
        raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")
    today = timezone.localdate()
    year = saln.filing_year(today)
    covered = saln.is_covered(person)
    if request.method == "POST":
        if not covered:
            raise PermissionDenied("Your appointment does not file a SALN.")
        if not saln.is_open(year, today):
            messages.error(request, f"The SALN for {year} can be filed from January 1, {year + 1}.")
            return redirect("saln_mine")
        with transaction.atomic():
            filing, _ = SalnFiling.objects.select_for_update().get_or_create(person=person, year=year)
            if not filing.reported_at:
                filing.reported_at = timezone.now()
                filing.save()
        messages.success(request, f"Recorded. HRMO will confirm when they have your SALN for {year}.")
        return redirect("saln_mine")
    return render(request, "hr/saln_mine.html", {
        "person": person, "year": year, "deadline": saln.deadline(year), "covered": covered, "today": today,
        "open": saln.is_open(year, today),
        "current": SalnFiling.objects.filter(person=person, year=year).first(),
        "filings": SalnFiling.objects.filter(person=person).order_by("-year"),
    })


# --- Overtime credits and CTO (CT1-CT5) ------------------------------------------------------


@login_required
def coc_card(request, person_pk):
    person = get_object_or_404(Person, pk=person_pk)
    if not can_view_leave_of(request.user, person):
        raise PermissionDenied("You cannot see this person's overtime credits.")
    today = timezone.localdate()
    balance = coc.balance(person, today)
    return render(request, "hr/coc_card.html", {
        "person": person, "today": today, "balance": balance, "held": coc.held_hours(person),
        "days": (balance / coc.HOURS_PER_DAY).quantize(Decimal("0.1")), "is_self": is_self(request.user, person),
        "lots": [l for l in coc.lots(person) if l["left"] > 0 and l["expires"] >= today],
        "entries": person.coc_entries.select_related("leave_request", "created_by").order_by("-date", "-pk"),
        "can_manage": can_manage_hr_records(request.user), "earns": coc.earns_coc(person),
        "monthly_cap": settings.COC_MONTHLY_CAP, "balance_cap": settings.COC_BALANCE_CAP,
        "valid_months": settings.COC_VALID_MONTHS,
    })


@login_required
def coc_record(request):
    """HRMO credits overtime for everyone an office order covered, within the caps (CT2)."""
    if is_sysadmin(request.user) and not request.user.is_superuser:
        raise PermissionDenied
    _require_manage(request)
    today = timezone.localdate()
    errors, values = {}, {}
    if request.method == "POST":
        values = {k: request.POST.get(k, "").strip() for k in ("worked_on", "hours", "day_kind", "reference")}
        try:
            worked_on = date.fromisoformat(values["worked_on"])
            if worked_on > today:
                errors["worked_on"] = "Overtime is credited after it is worked."
        except ValueError:
            errors["worked_on"] = "Give the day worked."
            worked_on = None
        try:
            hours = Decimal(values["hours"]).quantize(Decimal("0.01"))
            if not Decimal("0.5") <= hours <= Decimal("24"):
                raise InvalidOperation
        except InvalidOperation:
            errors["hours"] = "Hours worked beyond the regular day, 0.5 to 24."
            hours = None
        if values["day_kind"] not in CocEntry.DayKind.values:
            errors["day_kind"] = "Choose the kind of day."
        if not values["reference"]:
            errors["reference"] = "Give the office order or certificate number."
        ids = [int(i) for i in request.POST.getlist("person") if i.isdigit()]
        people = list(_not_self(request.user, coc.eligible_people().filter(pk__in=ids)))
        if not people:
            errors["people"] = "Tick everyone who worked the overtime."
        if not errors:
            capped = []
            credited_people = 0
            with transaction.atomic():
                for person in people:
                    entry, credited, short = coc.credit(person, worked_on, hours, values["day_kind"],
                                                        values["reference"], request.user, today)
                    credited_people += 1 if entry else 0
                    if short:
                        capped.append(f"{person.full_name} ({short} h)")
            messages.success(request, f"Credited overtime to {credited_people} "
                                      f"{'person' if credited_people == 1 else 'people'}.")
            if capped:
                messages.warning(request, "Over the monthly or total limit, not credited: " + "; ".join(capped) + ".")
            return redirect("coc_record")
    return render(request, "hr/coc_record.html", {
        "errors": errors, "values": values, "day_kinds": CocEntry.DayKind.choices,
        "people": _picker_people(request.user, exclude_types=settings.COC_EXCLUDED_TYPES), "ticked": set(request.POST.getlist("person")),
        "workday_rate": settings.COC_WORKDAY_RATE, "restday_rate": settings.COC_RESTDAY_RATE,
        "monthly_cap": settings.COC_MONTHLY_CAP, "balance_cap": settings.COC_BALANCE_CAP,
        "valid_months": settings.COC_VALID_MONTHS, "today": today,
        "recent": CocEntry.objects.filter(kind=CocEntry.Kind.EARNED).select_related("person").order_by("-created_at")[:10],
    })
