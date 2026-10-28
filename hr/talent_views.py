"""Awards and recognition (AW1-AW3), Individual Development Plans (ID1-ID3) and the workforce
profile report (WF1-WF3)."""

from collections import Counter
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .audit import log_audit_event
from .forms import AwardForm, DevelopmentPlanForm
from .models import (
    AuditLog, Award, DevelopmentItem, DevelopmentPlan, Engagement, Office, PdsProfile, Person, SalaryAction,
)
from .permissions import (
    can_manage_awards, can_manage_hr_records, can_review_development_plan, can_view_awards,
    can_view_development_plan, can_view_trackers, can_view_workforce_report, is_self,
)
from .tracker_views import _picker_people
from .views import _xlsx_response


def _own_person(user):
    profile = getattr(user, "profile", None)
    return profile.person if profile and profile.person_id else None


# --- Awards (AW1-AW3) -------------------------------------------------------------------------


@login_required
def award_list(request):
    if not can_view_awards(request.user):
        raise PermissionDenied
    today = timezone.localdate()
    years = sorted({d.year for d in Award.objects.dates("given_on", "year")} | {today.year}, reverse=True)
    try:
        year = int(request.GET.get("year", today.year))
    except ValueError:
        year = today.year
    category = request.GET.get("category", "")
    rows = Award.objects.filter(given_on__year=year).select_related("person")
    if category in Award.Category.values:
        rows = rows.filter(category=category)
    return render(request, "hr/award_list.html", {
        "rows": rows, "year": year, "years": years, "category": category, "categories": Award.Category.choices,
        "can_manage": can_manage_awards(request.user),
    })


@login_required
def award_record(request):
    """One award, given to everyone ticked (AW1). From an issued loyalty case it starts filled in."""
    if not can_manage_awards(request.user):
        raise PermissionDenied("Only HRMO records awards.")
    case = None
    if request.GET.get("salary_action", "").isdigit():
        case = SalaryAction.objects.filter(pk=int(request.GET["salary_action"]), kind=SalaryAction.Kind.LOYALTY,
                                           status=SalaryAction.Status.ISSUED).select_related("person").first()
    initial, ticked = {"given_on": timezone.localdate(), "given_by": "Municipal Mayor"}, set()
    if case:
        initial.update(category=Award.Category.LOYALTY, title=f"{case.years}-year loyalty award",
                       given_on=case.signed_on or timezone.localdate(), basis=case.nosi_number)
        ticked = {str(case.person_id)}
    form = AwardForm(request.POST or None, initial=initial)
    errors = {}
    if request.method == "POST":
        ticked = set(request.POST.getlist("person"))
        own = _own_person(request.user)
        people = list(Person.objects.filter(pk__in=[int(i) for i in ticked if i.isdigit()])
                      .exclude(pk=own.pk if own else None))
        if not people:
            errors["people"] = "Tick everyone who received the award."
        if form.is_valid() and not errors:
            with transaction.atomic():
                for person in people:
                    Award.objects.create(person=person, created_by=request.user,
                                         salary_action=case if case and case.person_id == person.pk else None,
                                         **form.cleaned_data)
            messages.success(request, f"Recorded \"{form.cleaned_data['title']}\" for {len(people)} "
                                      f"{'person' if len(people) == 1 else 'people'}.")
            return redirect(f"/awards/?year={form.cleaned_data['given_on'].year}")
    return render(request, "hr/award_form.html", {
        "form": form, "errors": errors, "people": _picker_people(request.user), "ticked": ticked, "case": case,
    })


@login_required
def award_print(request, pk):
    """A certificate of recognition to print and sign (AW3)."""
    award = get_object_or_404(Award.objects.select_related("person"), pk=pk)
    if not (can_manage_awards(request.user) or is_self(request.user, award.person)):
        raise PermissionDenied("HRMO prints award certificates.")
    log_audit_event(action=AuditLog.Action.EXPORT, model_name="Award", object_id=award.pk,
                    object_repr=f"Award certificate (print): {award}"[:255], request=request)
    eng = award.person.engagements.filter(status=Engagement.Status.ACTIVE).select_related("office").first()
    return render(request, "hr/award_print.html", {"award": award, "eng": eng, "mayor": settings.LEAVE_FORM_MAYOR})


# --- Individual Development Plans (ID1-ID3) ---------------------------------------------------


@login_required
def idp_mine(request):
    person = _own_person(request.user)
    if person is None:
        if can_view_trackers(request.user):
            return redirect("idp_tracker")
        raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")
    return redirect("idp_person", pk=person.pk)


def _plan_for(person, year):
    return DevelopmentPlan.objects.filter(person=person, year=year).prefetch_related("items").first()


@login_required
def idp_person(request, pk):
    """The person's plan for a year: they (or HRMO) write it, send it, and their office head reviews it."""
    person = get_object_or_404(Person, pk=pk)
    if not can_view_development_plan(request.user, person):
        raise PermissionDenied("You cannot see this development plan.")
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or request.POST.get("year") or today.year)
    except ValueError:
        year = today.year
    can_edit = is_self(request.user, person) or (can_manage_hr_records(request.user) and not is_self(request.user, person))
    can_review = can_review_development_plan(request.user, person)
    plan = _plan_for(person, year)
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("goal", "add", "remove", "toggle", "submit") and not can_edit:
            raise PermissionDenied("Only the employee or HRMO edits the plan.")
        if action == "review" and not can_review:
            raise PermissionDenied("The office head or HRMO reviews the plan.")
        with transaction.atomic():
            plan, _created = DevelopmentPlan.objects.select_for_update().get_or_create(person=person, year=year)
            if action == "goal":
                form = DevelopmentPlanForm(request.POST)
                if form.is_valid():
                    plan.career_goal = form.cleaned_data["career_goal"]
            elif action == "add":
                competency = request.POST.get("competency", "").strip()
                activity = request.POST.get("activity", "").strip()
                if not (competency and activity):
                    messages.error(request, "Give the competency and the development activity.")
                    return redirect(f"/people/{pk}/development-plan/?year={year}")
                target = None
                try:
                    target = date.fromisoformat(request.POST.get("target_date", ""))
                except ValueError:
                    pass
                DevelopmentItem.objects.create(plan=plan, competency=competency[:200], activity=activity[:300],
                                               objective=request.POST.get("objective", "").strip()[:300],
                                               support=request.POST.get("support", "").strip()[:200], target_date=target)
            elif action in ("remove", "toggle"):
                item = get_object_or_404(DevelopmentItem, pk=request.POST.get("item"), plan=plan)
                if action == "remove":
                    item.delete()
                else:
                    item.done = not item.done
                    item.save()
            elif action == "submit":
                if not plan.items.exists():
                    messages.error(request, "Add at least one development activity first.")
                    return redirect(f"/people/{pk}/development-plan/?year={year}")
                plan.status, plan.submitted_at = DevelopmentPlan.Status.SUBMITTED, timezone.now()
            elif action == "review":
                plan.status, plan.reviewed_by, plan.reviewed_at = DevelopmentPlan.Status.REVIEWED, request.user, timezone.now()
                plan.review_note = request.POST.get("review_note", "").strip()
            else:
                raise PermissionDenied("Unknown action.")
            if action in ("goal", "add", "remove") and plan.status == DevelopmentPlan.Status.REVIEWED:
                plan.status = DevelopmentPlan.Status.DRAFT  # a changed plan is reviewed again
            plan.version += 1
            plan.save()
        messages.success(request, {"goal": "Career goal saved.", "add": "Activity added.", "remove": "Activity removed.",
                                   "toggle": "Updated.", "submit": "Sent to your office head for review.",
                                   "review": "Marked as reviewed."}[action])
        return redirect(f"/people/{pk}/development-plan/?year={year}")
    return render(request, "hr/idp_person.html", {
        "person": person, "plan": plan, "year": year, "years": range(today.year - 2, today.year + 2),
        "can_edit": can_edit, "can_review": can_review and plan is not None and plan.status == DevelopmentPlan.Status.SUBMITTED,
        "goal_form": DevelopmentPlanForm(initial={"career_goal": plan.career_goal if plan else ""}),
        "trainings": person.pds_training.filter(date_from__year=year).order_by("-date_from"),
    })


@login_required
def idp_tracker(request):
    """Who has a development plan this year, by office (ID3)."""
    if not can_view_trackers(request.user):
        raise PermissionDenied("This list is for HRMO and Auditor.")
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
    except ValueError:
        year = today.year
    engs = (Engagement.objects.filter(status=Engagement.Status.ACTIVE, type__in=settings.SPMS_RATED_TYPES)
            .select_related("person", "office").order_by("office__name", "person__last_name"))
    plans = {p.person_id: p for p in DevelopmentPlan.objects.filter(year=year)}
    offices, seen = {}, set()
    for eng in engs:
        if eng.person_id in seen:
            continue
        seen.add(eng.person_id)
        row = offices.setdefault(eng.office_id, {"office": eng.office, "people": [], "counts": Counter()})
        plan = plans.get(eng.person_id)
        row["people"].append((eng, plan))
        row["counts"][plan.status if plan else "none"] += 1
    totals = Counter()
    for row in offices.values():
        totals.update(row["counts"])
    return render(request, "hr/idp_tracker.html", {
        "offices": list(offices.values()), "year": year, "years": range(today.year - 2, today.year + 2),
        "totals": totals, "people": len(seen), "tracker": "idp",
    })


# --- Workforce profile (WF1-WF3) --------------------------------------------------------------

AGE_BANDS = [(0, 29, "Below 30"), (30, 39, "30–39"), (40, 49, "40–49"), (50, 59, "50–59"), (60, 200, "60 and over")]
TENURE_BANDS = [(0, 0, "Below 1 year"), (1, 4, "1–4 years"), (5, 9, "5–9 years"), (10, 19, "10–19 years"),
                (20, 200, "20 years or more")]


def _band(value, bands):
    return next(label for low, high, label in bands if low <= value <= high)


def _years(start, today):
    return today.year - start.year - ((today.month, today.day) < (start.month, start.day))


def workforce_profile(today, office=None):
    """Counts of people in active service by sex: appointment type, office, age, length of service in
    the LGU, and the PDS answers for persons with disability, solo parents and indigenous groups (WF1)."""
    engs = (Engagement.objects.filter(status=Engagement.Status.ACTIVE).select_related("person", "office")
            .order_by("person_id", "-start_date"))
    if office:
        engs = engs.filter(office=office)
    current = {}
    for eng in engs:
        current.setdefault(eng.person_id, eng)
    first_start = {}
    for person_id, start in Engagement.objects.filter(person_id__in=current).values_list("person_id", "start_date"):
        first_start[person_id] = min(first_start.get(person_id, start), start)
    pds = {p.person_id: p for p in PdsProfile.objects.filter(person_id__in=current)}

    def table(key, order=None):
        """[(group, Counter of F / M / U (not recorded) / total)], in `order` when given."""
        counts = {}
        for pid, eng in current.items():
            k = key(pid, eng)
            if k is None:
                continue
            row = counts.setdefault(k, Counter())
            row[eng.person.sex or "U"] += 1
            row["total"] += 1
        return [(k, counts.get(k, Counter())) for k in (order if order is not None else sorted(counts))]

    types = dict(Engagement.Type.choices)
    return {
        "total": len(current),
        "sex": Counter(e.person.sex or "U" for e in current.values()),
        "by_type": [(types[k], c) for k, c in table(lambda pid, e: e.type, list(types))],
        "by_office": table(lambda pid, e: e.office.name),
        "by_age": table(lambda pid, e: _band(_years(e.person.birthdate, today), AGE_BANDS),
                        [label for _l, _h, label in AGE_BANDS]),
        "by_tenure": table(lambda pid, e: _band(_years(first_start[pid], today), TENURE_BANDS),
                           [label for _l, _h, label in TENURE_BANDS]),
        "pds": [
            ("Persons with disability", table(lambda pid, e: "yes" if getattr(pds.get(pid), "q40b", None) else None)),
            ("Solo parents", table(lambda pid, e: "yes" if getattr(pds.get(pid), "q40c", None) else None)),
            ("Members of an indigenous group", table(lambda pid, e: "yes" if getattr(pds.get(pid), "q40a", None) else None)),
        ],
        "pds_count": len(pds),
    }


@login_required
def workforce_report(request):
    if not can_view_workforce_report(request.user):
        raise PermissionDenied("The workforce profile is for HRMO and Auditor.")
    today = timezone.localdate()
    office = None
    if request.GET.get("office", "").isdigit():
        office = Office.objects.filter(pk=int(request.GET["office"])).first()
    profile = workforce_profile(today, office)
    if request.GET.get("export") == "xlsx":
        rows = []
        for section, data in (("Appointment type", profile["by_type"]), ("Office", profile["by_office"]),
                              ("Age", profile["by_age"]), ("Length of service", profile["by_tenure"])):
            for label, c in data:
                rows.append([section, label, c["F"], c["M"], c["U"], c["total"]])
        for label, data in profile["pds"]:
            c = data[0][1] if data else Counter()
            rows.append(["PDS question 40", label, c["F"], c["M"], c["U"], c["total"]])
        return _xlsx_response(request, f"workforce-profile-{today}.xlsx", "Workforce profile",
                              ["Section", "Group", "Female", "Male", "Sex not recorded", "Total"], rows,
                              f"Workforce profile {today} (Excel)", model_name="Person")
    return render(request, "hr/workforce_report.html", {
        "p": profile, "today": today, "office": office, "offices": Office.objects.order_by("name"),
    })
