"""Pass slip screens: file one, the signature inbox, the slip itself with its times, the printout and
HRMO's monthly log (DECISIONS PS1-PS5)."""

from datetime import date, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import pass_slips
from .audit import log_audit_event
from .forms import PassSlipForm
from .models import AuditLog, Office, PassSlip, Person
from .permissions import (
    can_manage_record_of, can_view_leave_of, can_view_pass_slip_log, is_hrmo, is_sysadmin,
)
from .views import _xlsx_response


def _no_sysadmin(request):
    if is_sysadmin(request.user) and not request.user.is_superuser:
        raise PermissionDenied("System administrators have no access to HR records.")


def _own_person(user):
    profile = getattr(user, "profile", None)
    return profile.person if profile and profile.person_id else None


@login_required
def my_slips(request):
    _no_sysadmin(request)
    person = _own_person(request.user)
    if person is None:
        if pass_slips.inbox(request.user) or can_view_pass_slip_log(request.user):
            return redirect("slip_inbox")
        raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")
    today = timezone.localdate()
    rows = person.pass_slips.prefetch_related("steps")
    return render(request, "hr/slip_mine.html", {
        "person": person, "today_slips": [s for s in rows if s.slip_date == today],
        "page": Paginator([s for s in rows if s.slip_date != today], 20).get_page(request.GET.get("page")),
    })


def _file(request, person, by_hrmo):
    form = PassSlipForm(request.POST or None, by_hrmo=by_hrmo,
                        initial={"slip_date": timezone.localdate(),
                                 "time_out": timezone.localtime().time().replace(second=0, microsecond=0)})
    if request.method == "POST" and form.is_valid():
        slip = form.save(commit=False)
        slip.person = person
        pass_slips.file_slip(slip, request.user)
        messages.success(request, "Pass slip sent for signature." if not by_hrmo else "Pass slip recorded.")
        return redirect("slip_detail", pk=slip.pk)
    return render(request, "hr/slip_form.html", {"form": form, "person": person, "by_hrmo": by_hrmo,
                                                 "steps": [pass_slips.Step.Kind(k).label for k in pass_slips.step_kinds()]})


@login_required
def slip_new(request):
    _no_sysadmin(request)
    person = _own_person(request.user)
    if person is None or not person.engagements.filter(status="active").exists():
        raise PermissionDenied("Pass slips are for people in active service.")
    return _file(request, person, by_hrmo=False)


@login_required
def slip_record(request, person_pk):
    """HRMO enters a paper pass slip for an employee without an account, or after the fact (PS2)."""
    _no_sysadmin(request)
    person = get_object_or_404(Person, pk=person_pk)
    if not can_manage_record_of(request.user, person):
        raise PermissionDenied("Only HRMO records pass slips from paper, and never their own.")
    return _file(request, person, by_hrmo=True)


def _visible(request, pk):
    slip = get_object_or_404(PassSlip.objects.select_related("person", "office", "filed_by"), pk=pk)
    own = _own_person(request.user)
    if not ((own and own.pk == slip.person_id) or can_view_leave_of(request.user, slip.person)):
        raise PermissionDenied("You cannot see this pass slip.")
    return slip, bool(own and own.pk == slip.person_id)


def _parse_time(value):
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@login_required
def slip_detail(request, pk):
    _no_sysadmin(request)
    slip, is_person = _visible(request, pk)
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            with transaction.atomic():
                locked = PassSlip.objects.select_for_update().get(pk=pk)
                if str(locked.version) != request.POST.get("version", ""):
                    raise pass_slips.SlipError("Someone acted on this pass slip while you had it open. Check it again.")
                if action in ("done", "disapproved"):
                    pass_slips.act(locked, request.user, action, request.POST.get("note", "").strip())
                elif action == "cancel":
                    pass_slips.cancel(locked, request.user)
                elif action in ("out", "back"):
                    pass_slips.record_time(locked, request.user, action)
                elif action == "times":
                    for which in ("out", "back"):
                        when = _parse_time(request.POST.get(which, ""))
                        if when:
                            pass_slips.record_time(locked, request.user, which, when)
                else:
                    raise pass_slips.SlipError("Unknown action.")
                locked.version += 1
                locked.save()
        except pass_slips.SlipError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, {"done": "Signed.", "disapproved": "Disapproved.", "cancel": "Cancelled.",
                                       "out": "Time out recorded. Press I'm back when you return.",
                                       "back": "Welcome back: time recorded.", "times": "Times saved."}[action])
        return redirect("slip_detail", pk=pk)
    current = slip.current_step
    allowed, paper_only = pass_slips.can_act(request.user, current) if current else (False, False)
    today = timezone.localdate()
    hrmo = is_hrmo(request.user) and not is_person
    return render(request, "hr/slip_detail.html", {
        "slip": slip, "steps": slip.steps.select_related("acted_by"), "current": current,
        "can_act": allowed, "paper_only": paper_only, "is_person": is_person,
        "can_cancel": slip.status in ("pending", "approved") and not slip.actual_out and (is_person or hrmo),
        "can_press": is_person and slip.status == "approved" and slip.slip_date == today,
        "can_fix_times": hrmo and slip.status == "approved",
    })


@login_required
def slip_print(request, pk):
    _no_sysadmin(request)
    slip, _is_person = _visible(request, pk)
    log_audit_event(action=AuditLog.Action.EXPORT, model_name="PassSlip", object_id=slip.pk,
                    object_repr=f"Pass slip (print): {slip}"[:255], request=request)
    return render(request, "hr/slip_print.html", {"slip": slip, "steps": slip.steps.select_related("acted_by")})


@login_required
def slip_inbox(request):
    _no_sysadmin(request)
    rows = pass_slips.inbox(request.user)
    if not rows and not can_view_pass_slip_log(request.user) and _own_person(request.user) is None:
        raise PermissionDenied("Nothing here is for you.")
    today = timezone.localdate()
    out_now = []
    if can_view_pass_slip_log(request.user):
        out_now = list(PassSlip.objects.filter(status=PassSlip.Status.APPROVED, slip_date=today,
                                               actual_out__isnull=False, actual_back__isnull=True)
                       .select_related("person", "office").order_by("actual_out"))
    return render(request, "hr/slip_inbox.html", {"rows": rows, "out_now": out_now,
                                                  "log": can_view_pass_slip_log(request.user)})


@login_required
def slip_log(request):
    """Every approved slip in a month, with personal time added up per person (PS4)."""
    _no_sysadmin(request)
    if not can_view_pass_slip_log(request.user):
        raise PermissionDenied("The pass slip log is kept by HRMO.")
    today = timezone.localdate()
    try:
        year, month = (int(x) for x in request.GET.get("month", "").split("-"))
        first = date(year, month, 1)
    except ValueError:
        first = today.replace(day=1)
    office = None
    if request.GET.get("office", "").isdigit():
        office = Office.objects.filter(pk=int(request.GET["office"])).first()
    slips, totals = pass_slips.month_log(first.year, first.month, office)
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"pass-slips-{first:%Y-%m}.xlsx", f"Pass slips {first:%Y-%m}",
            ["Date", "Last name", "First name", "Office", "Kind", "Destination", "Purpose", "Time out (planned)",
             "Expected back", "Actually out", "Actually back", "Minutes out"],
            [[s.slip_date, s.person.last_name, s.person.first_name, s.office.name if s.office else "",
              s.get_kind_display(), s.destination, s.purpose, s.time_out, s.expected_back, s.actual_out,
              s.actual_back, s.minutes_out] for s in slips],
            f"Pass slip log {first:%B %Y} (Excel)", model_name="PassSlip",
        )
    return render(request, "hr/slip_log.html", {
        "slips": slips, "totals": totals, "first": first, "office": office,
        "offices": Office.objects.order_by("name"),
        "qs": f"month={first:%Y-%m}" + (f"&office={office.pk}" if office else ""),
    })
