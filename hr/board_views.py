"""Screens for leave (CS Form No. 6), the Plantilla of Personnel, vacancies and memos, and the
My HR home page (DECISIONS L1-L9, RP1, V1-V5, M1-M4)."""

import os
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import board, leave, meetings, qualifications, reminders, spms
from .audit import log_audit_event
from .forms import (
    ApplicantForm, LeaveCreditForm, LeaveRequestForm, MemoForm, VacancyForm, sanitize_filename,
)
from .models import (
    Applicant, AuditLog, CalendarEvent, CertificateRequest, Engagement, PdsEligibility, PdsSubmission, LeaveApprovalStep, LeaveCredit, LeaveRequest, LeaveType,
    Memo, MemoAcknowledgment, Office, Person, SalaryAction, Vacancy,
)
from .permissions import (
    can_approve_leave, can_manage_hr_records, can_manage_record_of, can_view_applicants, can_view_leave_of, can_view_leave_register,
    can_view_memo, can_view_plantilla_report, can_view_trackers, can_view_vacancies, get_user_office,
    has_all_office_scope, is_auditor, is_executive, is_hrmo, is_office_head, is_office_staff, is_sysadmin,
    memo_ack_scope, memo_is_for,
)
from .salary_actions import add_years
from .views import _xlsx_response

Step = LeaveApprovalStep


def _own_person(user):
    profile = getattr(user, "profile", None)
    return profile.person if profile and profile.person_id else None


def _no_sysadmin(request):
    if is_sysadmin(request.user) and not request.user.is_superuser:
        raise PermissionDenied("System administrators have no access to HR records.")


def _decimal(value):
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


# --- My HR home page (L8) ----------------------------------------------------------------


AHEAD_DAYS = 45  # how far "Coming up" and "contracts ending" look ahead


def _role_label(user, office):
    if user.is_superuser:
        return "Superuser"
    if is_hrmo(user):
        return "HRMO Staff"
    if is_auditor(user):
        return "Auditor · read-only"
    if is_executive(user):
        return "Executive"
    if is_office_head(user):
        return f"Office Head · {office.acronym or office.name}" if office else "Office Head"
    if is_office_staff(user):
        return "Office Staff"
    return "Employee"


def _my_panel(person, today):
    """The employee's own appointment and leave card (L8). No salary grade or step: pay data (R6)."""
    eng = leave.current_engagement(person)
    if eng:
        eng = Engagement.objects.select_related("office", "reports_to__person").get(pk=eng.pk)
    contract = None
    if eng and eng.end_date:
        total = max((eng.end_date - eng.start_date).days, 1)
        left = (eng.end_date - today).days
        contract = {"left": left, "pct": min(max(round(100 * (total - max(left, 0)) / total), 0), 100),
                    "soon": left <= AHEAD_DAYS}
    summary = leave.summary(person, today)
    return {
        "summary": summary,
        "open": LeaveRequest.objects.filter(person=person, status=LeaveRequest.Status.PENDING)
                .prefetch_related("steps").order_by("start_date"),
        "upcoming": LeaveRequest.objects.filter(person=person, status=LeaveRequest.Status.APPROVED,
                                                end_date__gte=today).order_by("start_date")[:3],
        "eng": eng,
        "contract": contract,
        "spl_pips": [i < summary["spl_left"] for i in range(int(summary["spl_allowance"]))],
        "forced_pct": min(round(100 * summary["forced_taken"] / leave.FORCED_LEAVE_DAYS), 100),
        "days_to_year_end": (summary["year_end"] - today).days,
    }


def _office_panel(office, today):
    """An Office Head's own office: who is away this week, leave in progress, contracts ending.
    The same facts the office calendar already shows them (DECISIONS R5)."""
    staff = Engagement.objects.filter(office=office, status=Engagement.Status.ACTIVE).values("person_id")
    week_end = today + timedelta(days=6)
    away = list(LeaveRequest.objects.filter(
        person__in=staff, status=LeaveRequest.Status.APPROVED, start_date__lte=week_end, end_date__gte=today,
    ).select_related("person").order_by("start_date", "person__last_name")[:8])
    return {
        "office": office,
        "staff": Person.objects.filter(pk__in=staff).count(),
        "away": away,
        "away_today": sum(1 for r in away if r.start_date <= today),
        "pending": LeaveRequest.objects.filter(person__in=staff, status=LeaveRequest.Status.PENDING).count(),
        "ending": [row for row in reminders.renewal_rows(today, office=office) if row["days"] <= AHEAD_DAYS][:5],
    }


def _workforce(today):
    """People in active service by appointment type, for all-office roles (names-free counts)."""
    counts = dict(Engagement.objects.filter(status=Engagement.Status.ACTIVE).values_list("type")
                  .annotate(n=Count("person", distinct=True)).values_list("type", "n"))
    total = Person.objects.filter(engagements__status=Engagement.Status.ACTIVE).distinct().count()
    # Fixed order so a type keeps its color when another type has nobody.
    parts = [{"type": value, "label": label, "n": counts[value], "pct": 100 * counts[value] / sum(counts.values())}
             for value, label in Engagement.Type.choices if counts.get(value)]
    return {
        "total": total,
        "parts": parts,
        "offices": Office.objects.filter(engagements__status=Engagement.Status.ACTIVE).distinct().count(),
        "on_leave_today": LeaveRequest.objects.filter(status=LeaveRequest.Status.APPROVED,
                                                      start_date__lte=today, end_date__gte=today).count(),
        "vacancies_open": Vacancy.objects.filter(status=Vacancy.Status.OPEN).count(),
        "vacancies_screening": Vacancy.objects.filter(status=Vacancy.Status.SCREENING).count(),
    }


def _todo(my, inbox, unread, hrmo, today, ready=0, pds_state=None, extra=()):
    """What needs this person, most urgent first. Each item: tone, count, label, url.
    HRMO's certify step is in the inbox already (leave.inbox), so it is not listed twice.
    `extra` holds (tone, count, singular, plural, url) items from the trackers."""
    items = []

    def add(tone, n, one, many, url=""):
        items.append({"tone": tone, "n": n, "label": one if n == 1 else many, "url": url})

    if inbox:
        add("warning", len(inbox), "leave application waiting for your signature",
            "leave applications waiting for your signature", reverse("leave_inbox"))
    if unread:
        late = ", at least one past its comply-by date" if any(m.comply_by and m.comply_by < today for m in unread) else ""
        add("danger" if late else "primary", len(unread), f"memo to read and acknowledge{late}",
            f"memos to read and acknowledge{late}", reverse("memo_list") + "?show=unread")
    if pds_state:
        if pds_state["returned"]:
            add("warning", 1, "Personal Data Sheet returned by HRMO for correction", "", reverse("pds_home"))
        elif pds_state["never"] and pds_state["missing"]:
            add("primary", len(pds_state["missing"]), "section of your Personal Data Sheet to fill in",
                "sections of your Personal Data Sheet to fill in", reverse("pds_home"))
        elif pds_state["never"]:
            add("primary", 1, "Personal Data Sheet ready to send to HRMO", "", reverse("pds_home"))
    if ready:
        add("primary", ready, "document ready for release at HRMO: bring a valid ID",
            "documents ready for release at HRMO: bring a valid ID", reverse("my_certificates"))
    if my and my["summary"]["eligible"] and my["summary"]["forced_left"]:
        by = f"{my['summary']['year_end']:%b %d}"
        add("warning", my["summary"]["forced_left"], f"day of forced leave to take by {by}",
            f"days of forced leave to take by {by}", reverse("leave_file"))
    if my and my["contract"] and my["contract"]["soon"]:
        left = my["contract"]["left"]
        if left < 0:
            add("danger", -left, "day since your appointment ended: ask HRMO about renewal",
                "days since your appointment ended: ask HRMO about renewal")
        else:
            add("warning", left, "day until your appointment ends: ask HRMO about renewal",
                "days until your appointment ends: ask HRMO about renewal")
    if hrmo:
        for key, tone, url, one, many in [
            ("lapsed", "danger", "tracker_renewals", "appointment past its end date and still active",
             "appointments past their end date and still active"),
            ("past_65", "danger", "tracker_retirement", "person past compulsory retirement age",
             "people past compulsory retirement age"),
            ("ending", "warning", "tracker_renewals", f"contract ending in the next {AHEAD_DAYS} days",
             f"contracts ending in the next {AHEAD_DAYS} days"),
            ("meetings", "warning", "tracker_meetings", "required meeting not yet held this period",
             "required meetings not yet held this period"),
            ("salary_due", "primary", "salary_action_list", "step increment or loyalty award due",
             "step increments or loyalty awards due"),
            ("certificates", "warning", "certificate_list", "service record or certificate to prepare",
             "service records or certificates to prepare"),
            ("pds", "warning", "pds_queue", "Personal Data Sheet to verify", "Personal Data Sheets to verify"),
        ]:
            if hrmo.get(key):
                add(tone, hrmo[key], one, many, reverse(url))
    for item in extra:
        add(*item)
    rank = {"danger": 0, "warning": 1, "primary": 2}
    return sorted(items, key=lambda i: rank[i["tone"]])


def _tracker_todo(person, hrmo, today):
    """Reminders from licences, SALN, overtime credits and ratings (EL1, SN2, CT3, SP4)."""
    from . import coc, saln

    items = []
    if person:
        due = qualifications.my_licences_due(person, today)
        if due:
            expired = any(e.license_valid_until < today for e in due)
            items.append(("danger" if expired else "warning", len(due),
                          "licence on your PDS expired or expires soon: renew it, then update your PDS",
                          "licences on your PDS expired or expire soon: renew them, then update your PDS",
                          reverse("pds_section", args=[person.pk, "eligibility"])))
        reminder = saln.my_reminder(person, today)
        if reminder and not (reminder["filing"] and reminder["filing"].reported_at):
            late = reminder["days"] < 0
            items.append(("danger" if late else "warning", 1,
                          f"SALN for {reminder['year']} {'overdue: file it with HRMO now' if late else 'to file with HRMO by ' + reminder['deadline'].strftime('%b %d')}",
                          "", reverse("saln_mine")))
        expiring = coc.expiring(person, today, 30)
        if expiring:
            hours = sum(l["left"] for l in expiring)
            items.append(("warning", hours, f"hour of overtime credit expires by {expiring[0]['expires']:%b %d}: use it as CTO",
                          f"hours of overtime credit expire by {expiring[0]['expires']:%b %d}: use them as CTO",
                          reverse("coc_card", args=[person.pk])))
    if hrmo and hrmo.get("can_manage"):
        licences = qualifications.licence_attention_count(today)
        if licences:
            items.append(("warning", licences, "licence expired or expiring soon", "licences expired or expiring soon",
                          reverse("tracker_licences")))
        year = saln.current_year(today)
        days_left = (saln.deadline(year) - today).days
        if saln.tracked(year) and days_left <= 30:
            missing = saln.missing_count(year)
            if missing:
                items.append(("danger" if days_left < 0 else "warning", missing,
                              f"person has not filed a SALN for {year}", f"people have not filed a SALN for {year}",
                              reverse("tracker_saln") + "?show=missing"))
        period = spms.last_ended(today)
        if (today - spms.period_bounds(*period)[1]).days > 30:
            unrated = spms.unrated_count(*period)
            if unrated:
                items.append(("primary", unrated, f"person without a rating for {spms.period_label(*period)}",
                              f"people without a rating for {spms.period_label(*period)}",
                              reverse("tracker_ratings") + f"?year={period[0]}&semester={period[1]}"))
    return items


def _coming_up(person, office, today):
    """Holidays, this office's own events and the person's approved leave, in date order."""
    until = today + timedelta(days=AHEAD_DAYS)
    shown = Q(kind=CalendarEvent.Kind.HOLIDAY, office__isnull=True)
    if office:
        shown |= Q(office=office)
    events = CalendarEvent.objects.filter(shown, start_date__range=(today, until)).order_by("start_date")[:6]
    rows = [{"date": e.start_date, "event": e, "kind": "holiday" if e.kind == CalendarEvent.Kind.HOLIDAY else "event"}
            for e in events]
    if person:
        rows += [{"date": max(r.start_date, today), "req": r, "kind": "leave"}
                 for r in LeaveRequest.objects.filter(person=person, status=LeaveRequest.Status.APPROVED,
                                                      end_date__gte=today, start_date__lte=until)]
    return sorted(rows, key=lambda r: r["date"])[:6]


def dashboard(request):
    """Everything an employee would otherwise walk to the HR office or the bulletin board for,
    plus the desk of each role: approvals, the office this week, or the whole workforce."""
    user = request.user
    today = timezone.localdate()
    person = _own_person(user)
    office = get_user_office(user)
    my = _my_panel(person, today) if person else None
    unread = list(board.unread_memos(person)[:5]) if person else []
    inbox = leave.inbox(user) if can_approve_leave(user) else []
    open_vacancies = list(Vacancy.objects.filter(status=Vacancy.Status.OPEN, deadline__gte=today)
                          .select_related("office").order_by("deadline")[:5])
    hrmo = None
    if can_view_leave_register(user):
        hrmo = {
            "pending": LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING).count(),
            "certify": LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING, steps__kind=Step.Kind.CERTIFY,
                                                   steps__status=Step.Status.PENDING).distinct().count(),
            "on_leave_today": LeaveRequest.objects.filter(status=LeaveRequest.Status.APPROVED,
                                                          start_date__lte=today, end_date__gte=today).count(),
            "vacancies": Vacancy.objects.filter(status__in=[Vacancy.Status.OPEN, Vacancy.Status.SCREENING]).count(),
            "memos": Memo.objects.filter(is_published=True, requires_ack=True).count(),
            "certificates": CertificateRequest.objects.filter(status=CertificateRequest.Status.PENDING).count()
                            if can_manage_hr_records(user) else 0,
            "pds": PdsSubmission.objects.filter(status=PdsSubmission.Status.SUBMITTED).count()
                   if can_manage_hr_records(user) else 0,
            "can_manage": can_manage_hr_records(user),
            "is_auditor": is_auditor(user) and not is_hrmo(user),
            "last_month": (today.replace(day=1) - timedelta(days=1)),
        }
        if can_view_trackers(user):
            active_until = Engagement.objects.filter(status=Engagement.Status.ACTIVE, end_date__isnull=False)
            hrmo.update({
                "lapsed": active_until.filter(end_date__lt=today).count(),
                "ending": len([r for r in reminders.renewal_rows(today) if 0 <= r["days"] <= AHEAD_DAYS]),
                "salary_due": SalaryAction.objects.filter(status=SalaryAction.Status.DUE).count(),
                "past_65": Person.objects.filter(
                    birthdate__lte=add_years(today, -reminders.COMPULSORY_AGE),
                    engagements__status=Engagement.Status.ACTIVE, engagements__type__in=reminders.GSIS_TYPES,
                ).distinct().count(),
                "meetings": meetings.attention_count(today),
            })
        if user.is_superuser or user.has_perm("hr.view_audit_log"):
            week = timezone.now() - timedelta(days=7)
            recent = AuditLog.objects.filter(timestamp__gte=week)
            hrmo["audit"] = {
                "changes": recent.filter(action__in=[AuditLog.Action.CREATE, AuditLog.Action.UPDATE,
                                                     AuditLog.Action.DELETE]).count(),
                "failed_logins": recent.filter(action=AuditLog.Action.FAILED_LOGIN).count(),
                "denied": recent.filter(action=AuditLog.Action.PERMISSION_DENIED).count(),
            }
    ready = (CertificateRequest.objects.filter(person=person, status=CertificateRequest.Status.READY).count()
             if person else 0)
    from . import pds

    todo = _todo(my, inbox, unread, hrmo, today, ready, pds.state(person) if person else None,
                 _tracker_todo(person, hrmo, today))
    return render(request, "hr/dashboard.html", {
        "person": person,
        "my": my,
        "inbox": inbox,
        "todo": todo,
        "unread": unread,
        "open_vacancies": open_vacancies,
        "coming_up": _coming_up(person, office, today),
        "hrmo": hrmo,
        "office_panel": _office_panel(office, today) if office and is_office_head(user) else None,
        "workforce": _workforce(today) if has_all_office_scope(user) else None,
        "role": _role_label(user, office),
        "today": today,
        "can_view_people": has_all_office_scope(user) or bool(office),
    })


# --- Leave: filing, approval, printing ------------------------------------------------------


def _leave_rules():
    """Filing guidance and the 6.B choices per leave type, for the form's script (json_script)."""
    return {
        "guidance": {str(k): v for k, v in leave.GUIDANCE.items()},
        "details": {str(k): [str(d) for d in v] for k, v in leave.DETAILS.items()},
        "specify": [str(d) for d in leave.DETAILS_TO_SPECIFY],
    }


@login_required
def my_leave(request):
    """The employee's leave card summary and applications."""
    _no_sysadmin(request)
    person = _own_person(request.user)
    if person is None:
        if can_view_leave_register(request.user):
            return redirect("leave_register")
        if can_approve_leave(request.user):
            return redirect("leave_inbox")
        raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")
    requests = person.leave_requests.prefetch_related("steps").order_by("-filed_at")
    return render(request, "hr/leave_mine.html", {
        "person": person,
        "summary": leave.summary(person),
        "page": Paginator(requests, 20).get_page(request.GET.get("page")),
    })


@login_required
def leave_days(request):
    """Working days between two dates, for the live count on the filing form (L4)."""
    from django.utils.dateparse import parse_date

    try:
        start, end = parse_date(request.GET.get("start", "")), parse_date(request.GET.get("end", ""))
    except ValueError:
        start = end = None
    if not start or not end or end < start or (end - start).days > 400:
        return JsonResponse({"days": 0, "holidays": []})
    off = sorted(d for d in leave.holidays_between(start, end) if d.weekday() < 5)
    return JsonResponse({"days": leave.working_days(start, end), "holidays": [d.isoformat() for d in off]})


@login_required
def leave_file(request):
    """File CS Form No. 6 online. HRMO can also enter a paper form for an employee (?paper=1)."""
    _no_sysadmin(request)
    for_hrmo = request.GET.get("paper") == "1" and can_manage_hr_records(request.user)
    person = None if for_hrmo else _own_person(request.user)
    if not for_hrmo:
        if person is None:
            raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")
        if leave.current_engagement(person) is None:
            raise PermissionDenied("Only people in active service can file leave.")
    if request.method == "POST":
        form = LeaveRequestForm(request.POST, person=person, for_hrmo=for_hrmo)
        if form.is_valid():
            req = form.save(commit=False)
            req.person = form.person_obj
            with transaction.atomic():
                leave.file_request(req, request.user)
            warnings = leave.check_filing(req, timezone.localdate())
            messages.success(request, f"Filed: {req.type_label}, {req.days} day(s) from {req.start_date:%b %d}. "
                                      "You can follow it here; print the form when you need a signed copy.")
            for warning in warnings:
                messages.warning(request, warning)
            if request.POST.get("then") == "print":
                return redirect(f"{reverse('leave_print', args=[req.pk])}?autoprint=1")
            return redirect("leave_detail", pk=req.pk)
    else:
        start = timezone.localdate() + timedelta(days=7)
        cto = request.GET.get("type") == LeaveType.CTO
        form = LeaveRequestForm(person=person, for_hrmo=for_hrmo,
                                initial={"start_date": start, "end_date": start,
                                         "leave_type": LeaveType.CTO if cto else LeaveType.VACATION,
                                         "detail": "" if cto else "within_ph"})
    return render(request, "hr/leave_form.html", {
        "form": form,
        "for_hrmo": for_hrmo,
        "person": person,
        "summary": leave.summary(person) if person else None,
        "leave_rules": _leave_rules(),
    })


def _step_turn_text(step, req):
    if step.kind == Step.Kind.RECOMMEND:
        return f"Waiting for the head of {req.office.name if req.office else 'the office'} to recommend it."
    if step.kind == Step.Kind.CERTIFY:
        return "Waiting for HRMO to certify the leave credits."
    return "Waiting for the Municipal Mayor (or authorized representative) to approve it."


@login_required
def leave_detail(request, pk):
    """One application: where it is, who signed, and the next signature for whoever's turn it is."""
    _no_sysadmin(request)
    req = get_object_or_404(LeaveRequest.objects.select_related("person", "office", "filed_by"), pk=pk)
    if not can_view_leave_of(request.user, req.person):
        raise PermissionDenied("You cannot see this leave application.")

    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            with transaction.atomic():
                locked = LeaveRequest.objects.select_for_update().get(pk=pk)
                if str(locked.version) != request.POST.get("version", ""):
                    raise leave.LeaveError("Someone acted on this application while you had it open. Nothing was "
                                           "saved; check the latest status below.")
                note = request.POST.get("note", "").strip()
                if action in ("done", "disapproved"):
                    with_pay = _decimal(request.POST.get("with_pay", "")) if "with_pay" in request.POST else None
                    without_pay = _decimal(request.POST.get("without_pay", "")) if "without_pay" in request.POST else None
                    if (with_pay is None) != (without_pay is None):
                        raise leave.LeaveError("Enter both the days with pay and without pay.")
                    leave.act(locked, request.user, action, note, with_pay, without_pay)
                elif action == "cancel":
                    leave.cancel(locked, request.user, note)
                else:
                    raise leave.LeaveError("Unknown action.")
                locked.version += 1
                locked.save()
        except leave.LeaveError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{locked.type_label} of {locked.person.full_name}: {locked.get_status_display()}"
                             + (f" · next: {locked.current_step.short_label}" if locked.current_step else "") + ".")
        return redirect("leave_detail", pk=pk)

    steps = list(req.steps.select_related("acted_by"))
    current = req.current_step
    allowed, paper_only = leave.can_act(request.user, current) if current else (False, False)
    with_pay, without_pay = leave.pay_split(req) if req.status == LeaveRequest.Status.PENDING else (None, None)
    is_applicant = _own_person(request.user) is not None and _own_person(request.user).pk == req.person_id
    can_cancel = (req.status == LeaveRequest.Status.PENDING and (is_applicant or is_hrmo(request.user))) or (
        req.status == LeaveRequest.Status.APPROVED and is_hrmo(request.user) and not is_applicant)
    return render(request, "hr/leave_detail.html", {
        "req": req,
        "steps": steps,
        "current": current,
        "turn_text": _step_turn_text(current, req) if current else "",
        "can_act": allowed,
        "paper_only": paper_only,
        "with_pay": with_pay,
        "without_pay": without_pay,
        "summary": leave.summary(req.person),
        "warnings": leave.check_filing(req, req.filed_at.date()) if req.status == LeaveRequest.Status.PENDING else [],
        "office_on_leave": leave.office_on_leave(req) if allowed else [],
        "can_cancel": can_cancel,
        "is_applicant": is_applicant,
        "credit_entries": req.credit_entries.order_by("pk"),
        "can_register": can_view_leave_register(request.user),
    })


@login_required
def _print_form(request, req, steps):
    """CS Form No. 6 laid out like the LGU's paper form, front and instructions (L6)."""
    from django.conf import settings

    balances = req.certified_balances or {}
    vl = _decimal(balances.get("vl", "")) if balances else None
    sl = _decimal(balances.get("sl", "")) if balances else None
    charge = leave.CHARGED_TO.get(req.leave_type)
    less_vl = req.days_with_pay if charge == LeaveType.VACATION and req.days_with_pay is not None else None
    less_sl = None
    if charge == LeaveType.SICK and req.days_with_pay is not None and sl is not None:
        less_sl = min(req.days_with_pay, max(sl, Decimal("0")))
        if req.days_with_pay > less_sl:
            less_vl = req.days_with_pay - less_sl
    types = [(value, label, leave.LEGAL_BASIS.get(value, "")) for value, label in LeaveType.choices
             if value not in (LeaveType.OTHER, LeaveType.CTO)]  # CTO goes under "Others" (CT4)
    return render(request, "hr/leave_print.html", {
        "req": req,
        "steps": steps,
        "types": types,
        "instructions": [(label, leave.GUIDANCE[value]) for value, label, _basis in types],
        "vl": vl, "sl": sl, "less_vl": less_vl, "less_sl": less_sl,
        "vl_balance": (vl - less_vl) if vl is not None and less_vl is not None else vl,
        "sl_balance": (sl - less_sl) if sl is not None and less_sl is not None else sl,
        "certifier": settings.LEAVE_FORM_CERTIFIER, "certifier_title": settings.LEAVE_FORM_CERTIFIER_TITLE,
        "mayor": settings.LEAVE_FORM_MAYOR,
        "autoprint": request.GET.get("autoprint") == "1",
        # Salary is left for the employee to write: pay is HRMO and Auditor only, even on one's own record (R6).
    })


@login_required
def leave_print(request, pk):
    """CS Form No. 6 filled in from the application, for wet signatures (F1)."""
    _no_sysadmin(request)
    req = get_object_or_404(LeaveRequest.objects.select_related("person", "office"), pk=pk)
    if not can_view_leave_of(request.user, req.person):
        raise PermissionDenied("You cannot see this leave application.")
    log_audit_event(action=AuditLog.Action.EXPORT, model_name="LeaveRequest", object_id=req.pk,
                    object_repr=f"CS Form No. 6 print: {req}"[:255], request=request)
    return _print_form(request, req, {s.kind: s for s in req.steps.select_related("acted_by")})


@login_required
def leave_print_blank(request):
    """A blank CS Form No. 6 with the employee's name, office and position, to fill in by hand (L6).
    HRMO can print one for anyone in active service with ?person=<id>."""
    _no_sysadmin(request)
    person = _own_person(request.user)
    if request.GET.get("person", "").isdigit() and can_manage_hr_records(request.user):
        person = get_object_or_404(Person, pk=int(request.GET["person"]))
    if person is None:
        raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")
    eng = leave.current_engagement(person)
    blank = LeaveRequest(person=person, office=eng.office if eng else None,
                         position_title=eng.position_title if eng else "")
    return _print_form(request, blank, {})


@login_required
def leave_inbox(request):
    """Applications waiting for the user's signature, oldest start date first."""
    _no_sysadmin(request)
    if not can_approve_leave(request.user):
        raise PermissionDenied("Only leave approvers have an approvals inbox.")
    rows = leave.inbox(request.user)
    clashes = {req.pk: leave.office_on_leave(req) for req in rows}
    return render(request, "hr/leave_inbox.html", {
        "rows": [(req, req.current_step, clashes[req.pk]) for req in rows],
        "today": timezone.localdate(),
    })


@login_required
def leave_register(request):
    """Every application, for HRMO and Auditor, with filters and an Excel export."""
    _no_sysadmin(request)
    if not can_view_leave_register(request.user):
        raise PermissionDenied("The leave register is for HRMO and Auditor.")
    qs = LeaveRequest.objects.select_related("person", "office").prefetch_related("steps").order_by("-filed_at")
    status = request.GET.get("status", "")
    if status in LeaveRequest.Status.values:
        qs = qs.filter(status=status)
    else:
        status = ""
    waiting = request.GET.get("waiting", "")
    if waiting in Step.Kind.values:
        qs = qs.filter(status=LeaveRequest.Status.PENDING, steps__kind=waiting, steps__status=Step.Status.PENDING)
        qs = [r for r in qs.distinct() if r.current_step and r.current_step.kind == waiting]
    else:
        waiting = ""
    leave_type = request.GET.get("type", "")
    office = None
    if request.GET.get("office", "").isdigit():
        office = Office.objects.filter(pk=int(request.GET["office"])).first()
    q = request.GET.get("q", "").strip()
    if not isinstance(qs, list):
        if leave_type in LeaveType.values:
            qs = qs.filter(leave_type=leave_type)
        else:
            leave_type = ""
        if office:
            qs = qs.filter(office=office)
        if q:
            qs = qs.filter(Q(person__last_name__icontains=q) | Q(person__first_name__icontains=q)
                           | Q(person__employee_no__icontains=q))
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"leave-register-{timezone.localdate():%Y-%m-%d}.xlsx", "Leave register",
            ["No.", "Employee no.", "Name", "Office", "Leave", "From", "To", "Days", "With pay", "Without pay",
             "Status", "Waiting for", "Filed"],
            [[r.pk, r.person.employee_no, r.person.full_name, r.office.name if r.office else "", r.type_label,
              r.start_date, r.end_date, r.days, r.days_with_pay, r.days_without_pay, r.get_status_display(),
              r.current_step.short_label if r.current_step else "", timezone.localtime(r.filed_at).date()]
             for r in qs],
            "Leave register (Excel)", model_name="LeaveRequest",
        )
    params = [(k, v) for k, v in (("status", status), ("waiting", waiting), ("type", leave_type),
                                  ("office", office.pk if office else ""), ("q", q)) if v]
    return render(request, "hr/leave_register.html", {
        "page": Paginator(qs, 50).get_page(request.GET.get("page")),
        "status": status, "statuses": LeaveRequest.Status.choices,
        "waiting": waiting, "waiting_choices": [(k, l) for k, l in Step.Kind.choices],
        "leave_type": leave_type, "types": LeaveType.choices,
        "office": office, "offices": Office.objects.order_by("name"),
        "q": q,
        "filter_qs": "&" + urlencode(params) if params else "",
        "export_qs": urlencode(params + [("export", "xlsx")]),
        "can_manage": can_manage_hr_records(request.user),
    })


@login_required
def leave_card(request, person_pk):
    """A person's leave ledger with running balances, like the paper leave card. HRMO adds entries."""
    _no_sysadmin(request)
    person = get_object_or_404(Person, pk=person_pk)
    if not can_view_leave_of(request.user, person):
        raise PermissionDenied("You cannot see this leave card.")
    can_manage = can_manage_record_of(request.user, person)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Only HRMO can change the leave ledger, and another HRMO staff member "
                                   "changes your own (R10).")
        form = LeaveCreditForm(request.POST)
        if form.is_valid():
            if not person.earns_leave_credits():
                messages.error(request, "Only people with an active leave-eligible appointment have leave credits (L3).")
            else:
                entry = form.save(commit=False)
                entry.person = person
                entry.save()
                messages.success(request, f"Added {entry.days:+} day(s) {entry.get_leave_type_display().lower()}.")
            return redirect("leave_card", person_pk=person.pk)
    else:
        form = LeaveCreditForm(initial={"as_of": timezone.localdate()})
    rows, running = [], {LeaveType.VACATION: Decimal("0"), LeaveType.SICK: Decimal("0")}
    for entry in LeaveCredit.objects.filter(person=person).select_related("leave_request").order_by("as_of", "pk"):
        running[entry.leave_type] = running.get(entry.leave_type, Decimal("0")) + entry.days
        rows.append({"entry": entry, "vl": running[LeaveType.VACATION], "sl": running[LeaveType.SICK]})
    rows.reverse()
    return render(request, "hr/leave_card.html", {
        "person": person,
        "rows": rows,
        "summary": leave.summary(person),
        "form": form,
        "can_manage": can_manage,
        "requests": person.leave_requests.order_by("-start_date")[:10],
    })


def _month_choices(today):
    first = today.replace(day=1)
    months = []
    for _ in range(12):
        first = (first - timedelta(days=1)).replace(day=1)
        months.append(first)
    return [today.replace(day=1)] + months


@login_required
def leave_post_credits(request):
    """HRMO posts the month's 1.25 + 1.25 days to every leave-eligible person, once per month (L2)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("Only HRMO can post leave credits.")
    today = timezone.localdate()
    choices = _month_choices(today)
    value = request.POST.get("month") or request.GET.get("month") or (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    month = next((m for m in choices if m.strftime("%Y-%m") == value), choices[1])
    if request.method == "POST":
        with transaction.atomic():
            people = leave.post_monthly_credits(month.year, month.month)
        messages.success(request, f"Posted {leave.MONTHLY_EARNING} days vacation and sick leave for "
                                  f"{month:%B %Y} to {len(people)} people.")
        return redirect(f"{reverse('leave_post_credits')}?month={month:%Y-%m}")
    preview = leave.post_monthly_credits(month.year, month.month, dry_run=True)
    already = leave.eligible_people().count() - len(preview)
    return render(request, "hr/leave_post_credits.html", {
        "month": month, "choices": choices, "preview": preview[:30], "count": len(preview), "already": already,
        "earning": leave.MONTHLY_EARNING,
    })


@login_required
def leave_balances(request):
    """The "Leave balances as of" list HRMO posts on the bulletin board, always current (L8)."""
    _no_sysadmin(request)
    if not can_view_leave_register(request.user):
        raise PermissionDenied("The leave balances list is for HRMO and Auditor.")
    from django.utils.dateparse import parse_date

    try:
        as_of = parse_date(request.GET.get("as_of", "")) or timezone.localdate()
    except ValueError:
        as_of = timezone.localdate()
    office = None
    if request.GET.get("office", "").isdigit():
        office = Office.objects.filter(pk=int(request.GET["office"])).first()
    rows = leave.balances_board(as_of, office)
    title = f"Leave balances as of {as_of:%B %d, %Y}"
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"leave-balances-{as_of:%Y-%m-%d}.xlsx", "Leave balances",
            ["No.", "Name", "Vacation leave", "Sick leave", "Total"],
            [[i, r["person"].full_name, r["vl"], r["sl"], r["total"]] for i, r in enumerate(rows, 1)],
            f"{title} (Excel)", model_name="LeaveCredit",
        )
    params = [(k, v) for k, v in (("as_of", as_of.isoformat()), ("office", office.pk if office else "")) if v]
    template = "hr/leave_balances_print.html" if request.GET.get("print") else "hr/leave_balances.html"
    if request.GET.get("print"):
        log_audit_event(action=AuditLog.Action.EXPORT, model_name="LeaveCredit", object_repr=f"{title} (print)",
                        request=request)
    return render(request, template, {
        "rows": rows, "as_of": as_of, "office": office, "offices": Office.objects.order_by("name"), "title": title,
        "qs": urlencode(params),
    })


# --- Plantilla of Personnel (RP1) ------------------------------------------------------------


@login_required
def plantilla_report(request):
    _no_sysadmin(request)
    if not can_view_plantilla_report(request.user):
        raise PermissionDenied("The Plantilla of Personnel shows salaries and birthdates: HRMO and Auditor only.")
    office = None
    if request.GET.get("office", "").isdigit():
        office = Office.objects.filter(pk=int(request.GET["office"])).first()
    groups = board.plantilla_rows(office)
    year = timezone.localdate().year
    if request.GET.get("export") == "xlsx":
        rows = []
        for off, items, _totals in groups:
            for r in items:
                p = r["person"]
                rows.append([off.name, r["item"], r["position"], r["sg"], r["annual"], r["step"],
                             p.last_name if p else "(Vacant)", p.first_name if p else "", p.middle_name if p else "",
                             p.birthdate if p else None, r.get("original"), r.get("promoted"),
                             r.get("status", "") if p else ""])
        return _xlsx_response(
            request, f"plantilla-of-personnel-{year}.xlsx", f"Plantilla {year}",
            ["Office", "Item no.", "Position title", "SG", "Annual salary (actual)", "Step", "Last name",
             "First name", "Middle name", "Date of birth", "Original appointment", "Last promotion", "Status"],
            rows, f"Plantilla of Personnel {year} (Excel)",
        )
    if request.GET.get("print"):
        log_audit_event(action=AuditLog.Action.EXPORT, model_name="Engagement",
                        object_repr=f"Plantilla of Personnel {year} (print)", request=request)
    totals = {
        "filled": sum(t["filled"] for _o, _r, t in groups),
        "vacant": sum(t["vacant"] for _o, _r, t in groups),
        "annual": sum(t["annual"] for _o, _r, t in groups),
    }
    return render(request, "hr/plantilla_print.html" if request.GET.get("print") else "hr/plantilla_report.html", {
        "groups": groups, "office": office, "offices": Office.objects.order_by("name"), "year": year,
        "totals": totals, "qs": urlencode([("office", office.pk)] if office else []),
        "as_of": timezone.localdate(),
    })


# --- Vacancies and HRMPSB (V1-V5) -------------------------------------------------------------


@login_required
def vacancy_list(request):
    _no_sysadmin(request)
    if not can_view_vacancies(request.user):
        raise PermissionDenied
    today = timezone.localdate()
    can_manage = can_manage_hr_records(request.user)
    qs = Vacancy.objects.select_related("office").annotate(applicant_count=Count("applicants"))
    tab = request.GET.get("tab", "open")
    tabs = [("open", "Open now"), ("screening", "Being screened"), ("filled", "Filled")]
    if can_manage:
        tabs += [("draft", "Drafts"), ("cancelled", "Cancelled")]
    if tab not in dict(tabs):
        tab = "open"
    rows = qs.filter(status=tab).order_by("deadline" if tab == "open" else "-created_at")
    person = _own_person(request.user)
    applied = set(Applicant.objects.filter(person=person).values_list("vacancy_id", flat=True)) if person else set()
    return render(request, "hr/vacancy_list.html", {
        "tabs": [(k, l, qs.filter(status=k).count()) for k, l in tabs], "tab": tab, "rows": rows,
        "today": today, "can_manage": can_manage, "applied": applied,
        "can_view_applicants": can_view_applicants(request.user),
    })


@login_required
def vacancy_form(request, pk=None):
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("Only HRMO can post vacancies.")
    vacancy = get_object_or_404(Vacancy, pk=pk) if pk else None
    if request.method == "POST":
        form = VacancyForm(request.POST, instance=vacancy)
        if form.is_valid():
            with transaction.atomic():
                if vacancy:
                    locked = Vacancy.objects.select_for_update().get(pk=vacancy.pk)
                    if locked.version != form.cleaned_data.get("version"):
                        form.add_error(None, "Someone changed this vacancy while you were editing. Reload and try again.")
                        return render(request, "hr/vacancy_form.html", {"form": form, "vacancy": vacancy})
                saved = form.save(commit=False)
                if vacancy:
                    saved.version = locked.version + 1
                else:
                    saved.created_by = request.user
                saved.save()
            messages.success(request, f"Saved {saved}.")
            return redirect("vacancy_detail", pk=saved.pk)
    else:
        initial = {} if vacancy else {
            "posted_at": "HRMO bulletin board, 2nd floor Old Municipal Building",
            "published_on": timezone.localdate(), "deadline": timezone.localdate() + timedelta(days=14),
        }
        form = VacancyForm(instance=vacancy, initial=initial)
    return render(request, "hr/vacancy_form.html", {"form": form, "vacancy": vacancy})


def _visible_vacancy(request, pk):
    vacancy = get_object_or_404(Vacancy.objects.select_related("office", "filled_by__person"), pk=pk)
    if not can_view_vacancies(request.user):
        raise PermissionDenied
    if vacancy.status not in Vacancy.VISIBLE_STATUSES and not can_manage_hr_records(request.user):
        raise Http404("No such vacancy.")
    return vacancy


@login_required
def vacancy_detail(request, pk):
    """The notice, an online "I'm interested" for employees, and HRMO's applicant list (V2, V3)."""
    _no_sysadmin(request)
    vacancy = _visible_vacancy(request, pk)
    today = timezone.localdate()
    can_manage = can_manage_hr_records(request.user)
    person = _own_person(request.user)
    applicant_form = ApplicantForm(initial={"signified_on": today})
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "apply":
            if person is None:
                raise PermissionDenied("Your account is not linked to a personnel record.")
            if not vacancy.is_accepting(today):
                messages.error(request, "This vacancy is no longer accepting applications.")
            else:
                # What is already on file counts toward the requirements (EL3, SP3); HRMO can untick.
                on_file = {
                    "has_pds": PdsSubmission.objects.filter(person=person, status=PdsSubmission.Status.VERIFIED).exists(),
                    "has_rating": bool(spms.recent_ratings(person, 1)),
                    "has_eligibility": PdsEligibility.objects.filter(person=person).exists(),
                }
                try:
                    with transaction.atomic():
                        Applicant.objects.create(
                            vacancy=vacancy, person=person, last_name=person.last_name, first_name=person.first_name,
                            middle_name=person.middle_name, mobile=person.mobile, email=person.email,
                            signified_on=today, applied_online=True, **on_file,
                        )
                except IntegrityError:
                    messages.info(request, "You already signified your interest in this vacancy.")
                else:
                    messages.success(request, "Your interest is recorded. Bring or send the documents listed below "
                                              "to HRMO before the deadline.")
            return redirect("vacancy_detail", pk=pk)
        if not can_manage:
            raise PermissionDenied("Only HRMO can record applicants.")
        if action == "add_applicant":
            applicant_form = ApplicantForm(request.POST)
            if applicant_form.is_valid():
                applicant = applicant_form.save(commit=False)
                applicant.vacancy = vacancy
                applicant.save()
                messages.success(request, f"Added {applicant.full_name}.")
                return redirect("vacancy_detail", pk=pk)
        else:
            raise PermissionDenied("Unknown action.")
    applicants = list(vacancy.applicants.select_related("person")) if can_view_applicants(request.user) else None
    for applicant in applicants or []:
        applicant.evidence = qualifications.applicant_evidence(applicant, vacancy, today)
    my_application = vacancy.applicants.filter(person=person).first() if person else None
    return render(request, "hr/vacancy_detail.html", {
        "vacancy": vacancy, "today": today, "can_manage": can_manage, "applicants": applicants,
        "applicant_form": applicant_form, "requirements": Applicant.REQUIREMENTS, "person": person,
        "my_application": my_application, "accepting": vacancy.is_accepting(today),
        "my_docs": [(label, getattr(my_application, field)) for field, label in Applicant.REQUIREMENTS]
                   if my_application else [],
        "days_left": (vacancy.deadline - today).days if vacancy.deadline else None,
    })


@login_required
def applicant_edit(request, pk):
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("Only HRMO can edit applicants.")
    applicant = get_object_or_404(Applicant.objects.select_related("vacancy"), pk=pk)
    form = ApplicantForm(request.POST or None, instance=applicant)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Saved {applicant.full_name}.")
        return redirect("vacancy_detail", pk=applicant.vacancy_id)
    return render(request, "hr/applicant_form.html", {"form": form, "applicant": applicant,
                                                      "requirements": Applicant.REQUIREMENTS})


@login_required
def vacancy_print(request, pk, which):
    """The CS request for publication, or the HRMPSB resolution draft, ready to print (V4)."""
    _no_sysadmin(request)
    vacancy = _visible_vacancy(request, pk)
    if which == "resolution":
        if not can_view_applicants(request.user):
            raise PermissionDenied("The resolution names applicants: HRMO, Auditor and the Executive.")
        log_audit_event(action=AuditLog.Action.EXPORT, model_name="Vacancy", object_id=vacancy.pk,
                        object_repr=f"HRMPSB resolution draft: {vacancy}"[:255], request=request)
        return render(request, "hr/vacancy_resolution_print.html", {
            "vacancy": vacancy, "draft": board.resolution_text(vacancy),
            "applicants": vacancy.applicants.all(), "posted_at": vacancy.posted_at,
        })
    if which != "publication":
        raise Http404
    return render(request, "hr/vacancy_publication_print.html", {"vacancy": vacancy, "requirements": Applicant.REQUIREMENTS})


# --- Memos (M1-M4) ------------------------------------------------------------------------


@login_required
def memo_list(request):
    _no_sysadmin(request)
    person = _own_person(request.user)
    can_manage = can_manage_hr_records(request.user)
    if has_all_office_scope(request.user):
        memos = Memo.objects.all() if can_manage else Memo.objects.filter(is_published=True)
    else:
        visible = Q(pk__in=board.memos_for_person(person).values("pk"))
        office = get_user_office(request.user)
        if office:
            visible |= Q(is_published=True, audience=Memo.Audience.OFFICE, office=office)
        memos = Memo.objects.filter(visible)
    acked = set(MemoAcknowledgment.objects.filter(person=person).values_list("memo_id", flat=True)) if person else set()
    addressed = set(board.memos_for_person(person).values_list("pk", flat=True)) if person else set()
    show = request.GET.get("show", "")
    memos = memos.annotate(ack_count=Count("acknowledgments", distinct=True)).order_by("-dated", "-pk")
    if show == "unread":
        memos = memos.filter(pk__in=addressed, requires_ack=True).exclude(pk__in=acked)
    return render(request, "hr/memo_list.html", {
        "page": Paginator(memos, 25).get_page(request.GET.get("page")),
        "acked": acked, "addressed": addressed, "show": show, "can_manage": can_manage,
        "unread_count": board.unread_memos(person).count() if person else 0,
        "today": timezone.localdate(),
    })


@login_required
def memo_form(request, pk=None):
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("Only HRMO can issue memos.")
    memo = get_object_or_404(Memo, pk=pk) if pk else None
    if request.method == "POST":
        form = MemoForm(request.POST, request.FILES, instance=memo)
        if form.is_valid():
            with transaction.atomic():
                if memo:
                    locked = Memo.objects.select_for_update().get(pk=memo.pk)
                    if locked.version != form.cleaned_data.get("version"):
                        form.add_error(None, "Someone changed this memo while you were editing. Reload and try again.")
                        return render(request, "hr/memo_form.html", {"form": form, "memo": memo})
                saved = form.save(commit=False)
                upload = request.FILES.get("scan")
                if upload:
                    saved.scan_name = sanitize_filename(upload.name)
                if memo:
                    saved.version = locked.version + 1
                else:
                    saved.created_by = request.user
                saved.save()
                form.save_m2m()
            messages.success(request, f"Saved {saved.number}.")
            return redirect("memo_detail", pk=saved.pk)
    else:
        form = MemoForm(instance=memo, initial={} if memo else {
            "dated": timezone.localdate(), "addressed_to": "All appointive employees and JO/COS personnel",
        })
    return render(request, "hr/memo_form.html", {"form": form, "memo": memo})


@login_required
def memo_detail(request, pk):
    """The memo and an "I have read and understood" button for its addressees (M2)."""
    _no_sysadmin(request)
    memo = get_object_or_404(Memo, pk=pk)
    if not can_view_memo(request.user, memo):
        raise PermissionDenied("This memo is not addressed to you.")
    person = _own_person(request.user)
    is_addressee = memo_is_for(memo, person)
    if request.method == "POST":
        if request.POST.get("action") != "ack" or not is_addressee or not memo.is_published:
            raise PermissionDenied("Only the people the memo is addressed to can acknowledge it.")
        try:
            with transaction.atomic():
                MemoAcknowledgment.objects.create(memo=memo, person=person, user=request.user)
        except IntegrityError:
            pass
        messages.success(request, f"Thank you. Your acknowledgment of {memo.number} is recorded.")
        return redirect("memo_detail", pk=pk)
    ack = memo.acknowledgments.filter(person=person).first() if person else None
    scope = memo_ack_scope(request.user)
    progress = None
    if scope is not False:
        people = board.memo_people(memo)
        if scope is not None:
            people = people.filter(engagements__office=scope, engagements__status=Engagement.Status.ACTIVE).distinct()
        total = people.count()
        done = memo.acknowledgments.filter(person__in=people).count()
        progress = {"total": total, "done": done, "pct": round(100 * done / total) if total else 0,
                    "office": scope}
    return render(request, "hr/memo_detail.html", {
        "memo": memo, "is_addressee": is_addressee, "ack": ack, "progress": progress,
        "can_manage": can_manage_hr_records(request.user), "today": timezone.localdate(),
    })


@login_required
def memo_acks(request, pk):
    """Who has and has not acknowledged, by office: the online routing slip (M3)."""
    _no_sysadmin(request)
    memo = get_object_or_404(Memo, pk=pk)
    scope = memo_ack_scope(request.user)
    if scope is False or not can_view_memo(request.user, memo):
        raise PermissionDenied("Acknowledgments are visible to HRMO, Auditor, executives and office heads.")
    can_manage = can_manage_hr_records(request.user)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Only HRMO can record signatures from the paper routing list.")
        allowed = set(board.memo_people(memo).values_list("pk", flat=True))
        done = set(memo.acknowledgments.values_list("person_id", flat=True))
        picked = [int(v) for v in request.POST.getlist("person") if v.isdigit() and int(v) in allowed - done]
        with transaction.atomic():
            for person_id in picked:
                MemoAcknowledgment.objects.create(memo=memo, person_id=person_id, user=request.user, from_paper=True)
        messages.success(request, f"Recorded {len(picked)} signature(s) from the paper list.")
        return redirect("memo_acks", pk=pk)
    office = scope
    if scope is None and request.GET.get("office", "").isdigit():
        office = Office.objects.filter(pk=int(request.GET["office"])).first()
    rows = board.ack_rows(memo, office)
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"memo-{memo.pk}-acknowledgments.xlsx", "Acknowledgments",
            ["Office", "Employee no.", "Name", "Position", "Acknowledged", "How"],
            [[r["office"].name if r["office"] else "", r["person"].employee_no, r["person"].full_name,
              r["eng"].position_title if r["eng"] else "",
              timezone.localtime(r["ack"].acknowledged_at).strftime("%Y-%m-%d %H:%M") if r["ack"] else "Not yet",
              ("Paper" if r["ack"].from_paper else "Online") if r["ack"] else ""] for r in rows],
            f"Acknowledgments of {memo.number} (Excel)", model_name="Memo",
        )
    pending_only = request.GET.get("pending") == "1"
    shown = [r for r in rows if not r["ack"]] if pending_only else rows
    return render(request, "hr/memo_acks.html", {
        "memo": memo, "rows": shown, "total": len(rows), "done": sum(1 for r in rows if r["ack"]),
        "office": office, "offices": Office.objects.order_by("name") if scope is None else None,
        "pending_only": pending_only, "can_manage": can_manage,
        "export_qs": urlencode([(k, v) for k, v in (("office", office.pk if office else ""), ("export", "xlsx")) if v]),
    })


@login_required
def memo_scan(request, pk):
    memo = get_object_or_404(Memo, pk=pk)
    if not can_view_memo(request.user, memo):
        raise PermissionDenied("This memo is not addressed to you.")
    if not memo.scan:
        raise Http404("No scan attached.")
    log_audit_event(action=AuditLog.Action.VIEW, model_name="Memo", object_id=memo.pk,
                    object_repr=f"Scan of {memo.number}"[:255], request=request)
    response = FileResponse(memo.scan.open("rb"), as_attachment=True,
                            filename=memo.scan_name or os.path.basename(memo.scan.name))
    response["X-Content-Type-Options"] = "nosniff"
    return response
