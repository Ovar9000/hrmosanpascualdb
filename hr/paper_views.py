"""Screens for the printed papers: appointment papers and the ROAI (AP1-AP6), job order and contract
of service papers and the JO/COS list (JC1-JC4), salary schedules with NOSA and NOSI (NS1-NS4)."""

from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import papers
from .audit import log_audit_event
from .forms import EngagementPapersForm, PlantillaItemForm, SalaryScheduleForm
from .models import AuditLog, Engagement, Office, PlantillaItem, SalaryAction, SalaryRate, SalarySchedule
from .permissions import can_manage_papers, can_manage_record_of, can_print_papers
from .views import _xlsx_response

# Which papers each kind of engagement gets (AP1, JC1).
PRINTS = {
    "appointment": ("Appointment (CS Form No. 33)", papers.CS33_TYPES, "hr/paper_appointment_print.html"),
    "oath": ("Oath of office", papers.OATH_TYPES, "hr/paper_oath_print.html"),
    "assumption": ("Certificate of assumption to duty", papers.APPOINTED_TYPES, "hr/paper_assumption_print.html"),
    "description": ("Position description", papers.APPOINTED_TYPES, "hr/paper_description_print.html"),
    "contract": ("Job order / contract of service", papers.CONTRACT_TYPES, "hr/paper_contract_print.html"),
}
MAX_BATCH = 300


def _require_print(request):
    if not can_print_papers(request.user):
        raise PermissionDenied("These papers show pay: HRMO and Auditor only.")


def _log(request, model, repr_, object_id=None):
    log_audit_event(action=AuditLog.Action.EXPORT, model_name=model, object_id=object_id,
                    object_repr=repr_[:255], request=request)


def _print_context(eng):
    today = timezone.localdate()
    context = {"today": today, "day": papers.ordinal_day(today.day)}
    context.update(papers.contract_context(eng) if eng.type in papers.CONTRACT_TYPES else papers.appointment_context(eng))
    return context


# --- One appointment's papers (AP1-AP4) ---------------------------------------------------------


@login_required
def engagement_papers(request, pk):
    """The details the papers need, and the papers this appointment or contract gets."""
    _require_print(request)
    eng = get_object_or_404(Engagement.objects.select_related("person", "office", "reports_to__person", "project"), pk=pk)
    can_edit = can_manage_papers(request.user) and can_manage_record_of(request.user, eng.person)
    form = EngagementPapersForm(instance=eng)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Only HRMO fills these in, and never on their own record.")
        form = EngagementPapersForm(request.POST, instance=eng)
        if form.is_valid():
            with transaction.atomic():
                locked = Engagement.objects.select_for_update().get(pk=pk)
                if locked.version != form.cleaned_data.get("version"):
                    form.add_error(None, "Someone changed this appointment while you had it open. Reload and try again.")
                else:
                    for field in form.Meta.fields:
                        setattr(locked, field, form.cleaned_data[field])
                    locked.version += 1
                    locked.save()
                    messages.success(request, "Saved. The papers below now print with these details.")
                    return redirect("engagement_papers", pk=pk)
    prints = [(key, label) for key, (label, types, _t) in PRINTS.items() if eng.type in types]
    return render(request, "hr/engagement_papers.html", {
        "eng": eng, "person": eng.person, "form": form, "can_edit": can_edit, "prints": prints,
        "missing": papers.missing_for_appointment(eng) if eng.type in papers.CS33_TYPES else [],
        "vacancy": papers.publication_for(eng),
    })


@login_required
def engagement_print(request, pk, which):
    _require_print(request)
    eng = get_object_or_404(Engagement.objects.select_related("person", "office", "reports_to__person", "project"), pk=pk)
    if which not in PRINTS or eng.type not in PRINTS[which][1]:
        raise PermissionDenied("This paper is not for this kind of appointment.")
    label, _types, template = PRINTS[which]
    _log(request, "Engagement", f"{label}: {eng.person}", eng.pk)
    context = _print_context(eng)
    context.update(items=[context.copy()], back=f"/engagements/{eng.pk}/papers/", autoprint=request.GET.get("autoprint") == "1")
    return render(request, template, context)


# --- Report on Appointments Issued (AP5) --------------------------------------------------------


def _month(value, today):
    try:
        year, month = (int(x) for x in value.split("-"))
        return date(year, month, 1)
    except (ValueError, AttributeError):
        return today.replace(day=1)


@login_required
def roai_report(request):
    _require_print(request)
    today = timezone.localdate()
    first = _month(request.GET.get("month", ""), today)
    rows = papers.roai_rows(first.year, first.month)
    label = first.strftime("%B %Y")
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"roai-{first:%Y-%m}.xlsx", f"ROAI {first:%Y-%m}",
            ["Date issued", "Last name", "First name", "Ext.", "Middle name", "Position title", "Item no.", "SG",
             "Monthly salary", "Employment status", "Period from", "Period to", "Nature of appointment",
             "Published from", "Published to", "Posted at", "Issue date recorded"],
            [[r["issued"], r["person"].last_name, r["person"].first_name, r["person"].suffix, r["person"].middle_name,
              r["eng"].position_title, r["eng"].item_number, r["eng"].salary_grade, r["monthly"], r["status"],
              r["period"][0] if r["period"] else None, r["period"][1] if r["period"] else None, r["nature"],
              r["vacancy"].published_on if r["vacancy"] else None, r["vacancy"].deadline if r["vacancy"] else None,
              r["vacancy"].posted_at if r["vacancy"] else "", "No (start date used)" if r["issued_guess"] else "Yes"]
             for r in rows],
            f"Report on Appointments Issued {label} (Excel)",
        )
    printing = bool(request.GET.get("print"))
    if printing:
        _log(request, "Engagement", f"Report on Appointments Issued {label} (print)")
    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return render(request, "hr/roai_print.html" if printing else "hr/roai_report.html", {
        "rows": rows, "first": first, "label": label, "prev": prev_month, "next": next_month,
        "guessed": sum(1 for r in rows if r["issued_guess"]), "today": today, "sign": papers.signatories(),
        "qs": urlencode({"month": f"{first:%Y-%m}"}),
    })


# --- Job orders and contracts of service (JC1-JC4) ----------------------------------------------


def _office(request):
    value = request.GET.get("office", "")
    return Office.objects.filter(pk=int(value)).first() if value.isdigit() else None


@login_required
def contract_list(request):
    """Job order, contract of service and project contracts to print, alone or many at once (JC2)."""
    _require_print(request)
    today = timezone.localdate()
    office = _office(request)
    show = request.GET.get("show", "active")
    qs = (Engagement.objects.filter(type__in=papers.CONTRACT_TYPES)
          .select_related("person", "office", "project").order_by("office__name", "person__last_name"))
    if show == "starting":
        qs = qs.filter(start_date__gte=today, start_date__lte=today + timedelta(days=60))
    else:
        show = "active"
        qs = qs.filter(status=Engagement.Status.ACTIVE)
    if office:
        qs = qs.filter(office=office)
    rows = list(qs)
    return render(request, "hr/contract_list.html", {
        "rows": rows, "office": office, "offices": Office.objects.order_by("name"), "show": show,
        "no_duties": sum(1 for e in rows if not e.duties), "max_batch": MAX_BATCH,
    })


@login_required
def contracts_print(request):
    """Many contracts in one printout, one per page (JC2)."""
    _require_print(request)
    ids = [int(i) for i in request.GET.getlist("id") if i.isdigit()][:MAX_BATCH]
    engs = list(Engagement.objects.filter(pk__in=ids, type__in=papers.CONTRACT_TYPES)
                .select_related("person", "office", "project").order_by("office__name", "person__last_name"))
    if not engs:
        messages.error(request, "Tick at least one contract to print.")
        return redirect("contract_list")
    today = timezone.localdate()
    items = []
    for eng in engs:
        item = papers.contract_context(eng)
        item.update(today=today, day=papers.ordinal_day(today.day))
        items.append(item)
    _log(request, "Engagement", f"{len(engs)} job order / contract of service papers (print)")
    return render(request, "hr/paper_contract_print.html", {
        "items": items, "back": "/papers/contracts/", "autoprint": False,
    })


@login_required
def jo_cos_report(request):
    """Job order and contract of service workers engaged in a semester, for the CSC report (JC3)."""
    _require_print(request)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
    except ValueError:
        year = today.year
    half = 2 if request.GET.get("half") == "2" or ("half" not in request.GET and today.month > 6) else 1
    start, end = papers.semester_bounds(year, half)
    office = _office(request)
    rows = papers.jo_cos_rows(start, end, office)
    for e in rows:
        e.fund = papers.fund_source(e)
    label = f"{'January to June' if half == 1 else 'July to December'} {year}"
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"jo-cos-{year}-{'h1' if half == 1 else 'h2'}.xlsx", f"JO-COS {year} S{half}",
            ["Office", "Last name", "First name", "Middle name", "Sex", "Designation", "Nature of work", "Type",
             "From", "To", "Rate", "Basis", "Fund source", "Status"],
            [[e.office.name, e.person.last_name, e.person.first_name, e.person.middle_name, e.person.sex,
              e.position_title, e.duties, e.get_type_display(), e.start_date, e.end_date, e.rate,
              e.get_rate_basis_display() if e.rate_basis else "", e.fund, e.get_status_display()]
             for e in rows],
            f"JO/COS list {label} (Excel)",
        )
    printing = bool(request.GET.get("print"))
    if printing:
        _log(request, "Engagement", f"JO/COS list {label} (print)")
    counts = {"total": len(rows), "female": sum(1 for e in rows if e.person.sex == "F"),
              "male": sum(1 for e in rows if e.person.sex == "M")}
    params = {"year": year, "half": half}
    if office:
        params["office"] = office.pk
    return render(request, "hr/jo_cos_print.html" if printing else "hr/jo_cos_report.html", {
        "rows": rows, "year": year, "half": half, "label": label, "office": office, "counts": counts,
        "offices": Office.objects.order_by("name"), "years": range(today.year - 3, today.year + 2),
        "qs": urlencode(params), "today": today, "sign": papers.signatories(),
    })


# --- Salary schedules, NOSA and NOSI (NS1-NS4) --------------------------------------------------


@login_required
def salary_schedule_list(request):
    _require_print(request)
    form = None
    if can_manage_papers(request.user):
        form = SalaryScheduleForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            with transaction.atomic():
                schedule = SalarySchedule.objects.create(
                    name=form.cleaned_data["name"], effective_date=form.cleaned_data["effective_date"],
                    basis=form.cleaned_data["basis"], created_by=request.user,
                )
                # One save per cell, never bulk_create (U6); the schedule itself is audit-logged.
                for (grade, step), monthly in form.cells.items():
                    SalaryRate.objects.create(schedule=schedule, salary_grade=grade, step=step, monthly=monthly)
            for warning in papers.schedule_warnings(form.cells)[:5]:
                messages.warning(request, f"Check the table: {warning}")
            messages.success(request, f"Saved {schedule.name}: {len(form.cells) // 8} salary grades.")
            return redirect("salary_schedule_detail", pk=schedule.pk)
    elif request.method == "POST":
        raise PermissionDenied("Only HRMO adds salary schedules.")
    today = timezone.localdate()
    return render(request, "hr/salary_schedule_list.html", {
        "schedules": SalarySchedule.objects.all(), "form": form, "current": papers.schedule_on(today),
    })


@login_required
def salary_schedule_detail(request, pk):
    """A schedule's table, and a NOSA for every plantilla appointment it changes (NS2)."""
    _require_print(request)
    schedule = get_object_or_404(SalarySchedule, pk=pk)
    office = _office(request)
    rows, previous = papers.nosa_rows(schedule, office)
    today = timezone.localdate()
    if request.method == "POST":
        if not can_manage_papers(request.user):
            raise PermissionDenied("Only HRMO updates stored salaries.")
        if request.POST.get("action") != "apply" or schedule.effective_date > today:
            messages.error(request, "Stored salaries can be updated once the schedule has taken effect.")
            return redirect("salary_schedule_detail", pk=pk)
        changed = 0
        with transaction.atomic():
            for row in rows:
                eng = row["eng"]
                if row["new"] is None or (eng.rate == row["new"] and eng.rate_basis == Engagement.RateBasis.MONTHLY):
                    continue
                if not can_manage_record_of(request.user, eng.person):
                    continue
                locked = Engagement.objects.select_for_update().get(pk=eng.pk)
                locked.rate, locked.rate_basis = row["new"], Engagement.RateBasis.MONTHLY
                note = f"Salary updated to {papers.peso(row['new'])} under {schedule.name}, effective {schedule.effective_date}."
                locked.notes = f"{locked.notes}\n{note}".strip()
                locked.version += 1
                locked.save()
                changed += 1
        messages.success(request, f"Updated the stored monthly salary of {changed} plantilla appointment(s).")
        return redirect("salary_schedule_detail", pk=pk)
    label = f"NOSA {schedule.name}"
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"nosa-{schedule.effective_date}.xlsx", "NOSA",
            ["Office", "Last name", "First name", "Position", "Item no.", "SG", "Step", "Salary before",
             "Adjusted salary", "Adjustment"],
            [[r["eng"].office.name, r["person"].last_name, r["person"].first_name, r["eng"].position_title,
              r["eng"].item_number, r["eng"].salary_grade, r["eng"].step, r["old"], r["new"], r["diff"]] for r in rows],
            f"{label} (Excel)",
        )
    if request.GET.get("print") == "nosa":
        _log(request, "Engagement", f"{label}: {len(rows)} notices (print)")
        return render(request, "hr/nosa_print.html", {
            "rows": rows, "schedule": schedule, "previous": previous, "today": today,
            "day_before": schedule.effective_date - timedelta(days=1), "sign": papers.signatories(),
        })
    return render(request, "hr/salary_schedule_detail.html", {
        "schedule": schedule, "grid": papers.grid(schedule), "rows": rows, "previous": previous,
        "office": office, "offices": Office.objects.order_by("name"), "today": today,
        "can_apply": can_manage_papers(request.user) and schedule.effective_date <= today,
        "to_update": sum(1 for r in rows if r["new"] is not None and r["stored"] != r["new"]),
        "missing": sum(1 for r in rows if r["new"] is None),
        "qs": urlencode({"office": office.pk}) if office else "",
    })


@login_required
def nosi_print(request, pk):
    """The Notice of Step Increment for a step increment case under review, awaiting signature or
    issued (NS3)."""
    _require_print(request)
    case = get_object_or_404(SalaryAction.objects.select_related("person", "engagement__office"), pk=pk)
    if not papers.can_print_nosi(case):
        raise PermissionDenied("A NOSI prints once the step increment is under review.")
    _log(request, "SalaryAction", f"NOSI: {case}", case.pk)
    today = timezone.localdate()
    context = papers.nosi_context(case)
    context.update(today=today)
    return render(request, "hr/nosi_print.html", context)


# --- Plantilla items (PI1-PI4) ----------------------------------------------------------------


@login_required
def plantilla_item_list(request):
    """Every plantilla item with its holder; HRMO adds items the Sangguniang Bayan creates (PI1)."""
    _require_print(request)
    office = _office(request)
    show = request.GET.get("show", "current")
    items = PlantillaItem.objects.select_related("office").order_by("office__name", "item_number")
    if show == "abolished":
        items = items.filter(status=PlantillaItem.Status.ABOLISHED)
    else:
        show = "current"
        items = items.exclude(status=PlantillaItem.Status.ABOLISHED)
    if office:
        items = items.filter(office=office)
    holders = {e.plantilla_item_id: e for e in Engagement.objects.filter(
        type=Engagement.Type.PLANTILLA, status=Engagement.Status.ACTIVE, plantilla_item__isnull=False,
    ).select_related("person")}
    form = None
    if can_manage_papers(request.user):
        form = PlantillaItemForm(request.POST or None, initial={"office": office.pk if office else None})
        if request.method == "POST" and form.is_valid():
            item = form.save()
            messages.success(request, f"Added item {item.item_number}. It shows as vacant on the Plantilla until filled.")
            return redirect("plantilla_item_detail", pk=item.pk)
    elif request.method == "POST":
        raise PermissionDenied("Only HRMO adds plantilla items.")
    rows = [(item, holders.get(item.pk)) for item in items]
    return render(request, "hr/plantilla_item_list.html", {
        "rows": rows, "office": office, "offices": Office.objects.order_by("name"), "show": show, "form": form,
        "vacant": sum(1 for _i, h in rows if h is None),
    })


@login_required
def plantilla_item_detail(request, pk):
    _require_print(request)
    item = get_object_or_404(PlantillaItem.objects.select_related("office"), pk=pk)
    can_edit = can_manage_papers(request.user)
    form = PlantillaItemForm(instance=item)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Only HRMO edits plantilla items.")
        form = PlantillaItemForm(request.POST, instance=item)
        if form.is_valid():
            with transaction.atomic():
                locked = PlantillaItem.objects.select_for_update().get(pk=pk)
                if locked.version != form.cleaned_data.get("version"):
                    form.add_error(None, "Someone changed this item while you had it open. Reload and try again.")
                else:
                    for field in form.Meta.fields:
                        setattr(locked, field, form.cleaned_data[field])
                    locked.version += 1
                    locked.save()
                    messages.success(request, "Saved.")
                    return redirect("plantilla_item_detail", pk=pk)
    return render(request, "hr/plantilla_item_detail.html", {
        "item": item, "form": form, "can_edit": can_edit,
        "history": item.engagements.select_related("person", "office").order_by("-start_date"),
    })
