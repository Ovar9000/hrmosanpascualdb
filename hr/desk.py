"""The HR Desk: every HRMO tool on one page, grouped by what HRMO is doing, with what needs attention
counted on each (DECISIONS DK1-DK3). The Tools menu shows only the desk link and the counted items, so it
stays short however many tools are added: a new tool is one entry in CARDS.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from . import meetings, modules, official_email, pass_slips
from .models import (
    CertificateRequest, Engagement, PasswordResetRequest, PdsSubmission, Person, SalaryAction, Separation,
)
from .permissions import (
    can_handle_password_resets, can_manage_hr_records, can_print_papers, can_view_awards, can_view_certificate_requests,
    can_view_leave_register, can_view_pass_slip_log, can_view_plantilla_report, can_view_salary_actions,
    can_view_email_register, can_view_separations, can_view_trackers, can_view_workforce_report,
)
from .reminders import COMPULSORY_AGE, GSIS_TYPES
from .salary_actions import add_years


def _lapsed(today):
    return Engagement.objects.filter(status=Engagement.Status.ACTIVE, end_date__lt=today).count()


def _past_retirement(today):
    return Person.objects.filter(
        birthdate__lte=add_years(today, -COMPULSORY_AGE), engagements__status=Engagement.Status.ACTIVE,
        engagements__type__in=GSIS_TYPES,
    ).distinct().count()


# The desk is for HRMO, the Auditor and the other tool roles; office heads and employees reach their own
# things from the People and Leave menus. Each card: (title, hint, items). Each item: (label, url name, who sees it, count or None, what the count is).
# A count is a function (user, today) -> int; anything above zero is shown as needing attention.
CARDS = [
    ("Hiring and appointments", "Someone applies, is appointed or is renewed.", [
        ("Vacant positions", "vacancy_list", can_view_trackers, None, ""),
        ("Contract renewals", "tracker_renewals", can_view_trackers, lambda u, t: _lapsed(t), "lapsed"),
        ("Job order & contract papers", "contract_list", can_print_papers, None, ""),
        ("Report on Appointments Issued", "roai_report", can_print_papers, None, ""),
        ("JO/COS list for CSC", "jo_cos_report", can_print_papers, None, ""),
        ("Projects", "project_list", lambda u: u.has_perm("hr.view_project"), None, ""),
    ]),
    ("Requests from employees", "Waiting at the HRMO counter or online.", [
        ("Service records & certificates", "certificate_list", can_view_certificate_requests,
         lambda u, t: CertificateRequest.objects.filter(status=CertificateRequest.Status.PENDING).count(), "to prepare"),
        ("Personal Data Sheets to verify", "pds_queue", can_view_certificate_requests,
         lambda u, t: PdsSubmission.objects.filter(status=PdsSubmission.Status.SUBMITTED).count(), "to verify"),
        ("Forgotten passwords", "reset_request_list", can_handle_password_resets,
         lambda u, t: PasswordResetRequest.objects.filter(status=PasswordResetRequest.Status.PENDING).count(), "waiting"),
    ]),
    ("Leave and time", "Leave cards, overtime and time out of the office.", [
        ("Leave register", "leave_register", can_view_leave_register, None, ""),
        ("Leave balances list", "leave_balances", can_view_leave_register, None, ""),
        ("Record overtime (COC)", "coc_record", lambda u: can_view_leave_register(u) and can_manage_hr_records(u), None, ""),
        ("Pass slips waiting for me", "slip_inbox", ("pass_slips", can_view_pass_slip_log),
         lambda u, t: len(pass_slips.inbox(u)), "to sign"),
        ("Pass slip log", "slip_log", ("pass_slips", can_view_pass_slip_log), None, ""),
    ]),
    ("Pay and benefits", "Step increments, salary adjustments and bonuses.", [
        ("Salary increases & loyalty", "salary_action_list", can_view_salary_actions,
         lambda u, t: SalaryAction.objects.filter(status=SalaryAction.Status.DUE).count(), "due"),
        ("Salary schedules & NOSA", "salary_schedule_list", can_print_papers, None, ""),
        ("Year-end bonus", "tracker_year_end_bonus", can_view_trackers, None, ""),
        ("Plantilla of Personnel", "plantilla_report", can_view_plantilla_report, None, ""),
        ("Plantilla items", "plantilla_item_list", can_print_papers, None, ""),
    ]),
    ("Performance and development", "Ratings, training and recognition.", [
        ("Performance ratings", "tracker_ratings", can_view_trackers, None, ""),
        ("Training hours", "tracker_learning", can_view_trackers, None, ""),
        ("Licences & eligibility", "tracker_licences", can_view_trackers, None, ""),
        ("Development plans", "idp_tracker", ("development_plans", can_view_trackers), None, ""),
        ("Awards & recognition", "award_list", ("awards", lambda u: can_view_awards(u) and can_view_trackers(u)), None, ""),
    ]),
    ("Compliance", "Filings and meetings the law requires.", [
        ("SALN filing", "tracker_saln", can_view_trackers, None, ""),
        ("Required meetings", "tracker_meetings", can_view_trackers,
         lambda u, t: meetings.attention_count(t), "to hold"),
    ]),
    ("Leaving the LGU", "Retirement, resignation and clearance.", [
        ("Retirement", "tracker_retirement", can_view_trackers, lambda u, t: _past_retirement(t), "past 65"),
        ("Separations & clearance", "separation_list", can_view_separations,
         lambda u, t: Separation.objects.filter(status=Separation.Status.CLEARING).count(), "in progress"),
        ("Official email accounts", "email_list", ("official_email", can_view_email_register),
         lambda u, t: official_email.attention_count(), "to reset or close"),
    ]),
    ("Records", "Reports about the whole workforce, and the audit trail.", [
        ("Workforce profile (GAD)", "workforce_report", ("workforce", can_view_workforce_report), None, ""),
        ("Audit trail", "audit_list", lambda u: u.has_perm("hr.view_audit_log") or u.is_superuser, None, ""),
        ("Admin", "admin:index", lambda u: u.is_staff, None, ""),
    ]),
]

# Counts that are a queue to work through, not an alarm: shown on the desk but not in the Tools menu.
QUIET = {"separation_list"}


def _sees(user, rule):
    if isinstance(rule, tuple):
        module, rule = rule
        if not modules.enabled(module):
            return False
    return rule(user)


def cards(user, today=None):
    """The cards this user sees, each with its visible items and their counts (DK1, DK2)."""
    if not user.is_authenticated:
        return []
    today = today or timezone.localdate()
    result = []
    for title, hint, items in CARDS:
        rows = []
        for label, name, rule, count, what in items:
            if not _sees(user, rule):
                continue
            rows.append({"label": label, "url": reverse(name), "name": name,
                         "count": count(user, today) if count else 0, "what": what, "quiet": name in QUIET})
        if rows:
            result.append({"title": title, "hint": hint, "items": rows})
    return result


def attention(desk):
    """Items with something waiting, for the Tools menu (DK3)."""
    return [i for card in desk for i in card["items"] if i["count"] and not i["quiet"]]


@login_required
def desk_page(request):
    desk = cards(request.user)
    if not desk:
        raise PermissionDenied("The HR Desk is for HRMO, the Auditor and office roles with tools.")
    return render(request, "hr/desk.html", {"desk": desk, "waiting": attention(desk)})
