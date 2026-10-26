"""Template context shared by every page."""

from django.conf import settings
from django.utils.functional import SimpleLazyObject
from django.utils import timezone
from .models import SalaryAction
from .meetings import needs_attention as meetings_need_attention
from .reminders import needs_attention
from .permissions import (
    can_approve_leave, can_view_certificate_requests, can_view_leave_register, can_view_office_tree, can_view_plantilla_report,
    can_view_salary_actions, get_user_office, is_sysadmin,
)


def org_chart_nav(request):
    """Whether to show the Org Chart and My Office menu links. Lazy: costs nothing on pages
    that never render the menu (DECISIONS O3)."""

    def links():
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return {"tree": False, "my_office": False, "salary": False}
        salary = can_view_salary_actions(user)
        return {
            "tree": can_view_office_tree(user),
            "my_office": get_user_office(user) is not None,
            "salary": salary,
            "salary_due": salary and SalaryAction.objects.filter(status=SalaryAction.Status.DUE).exists(),
            "attention": salary and (
                needs_attention(timezone.localdate()) or meetings_need_attention(timezone.localdate())
            ),
        }

    return {"org_nav": SimpleLazyObject(links)}


def board_nav(request):
    """Leave, memo and vacancy menu links with their counts (DECISIONS L8, M2). Lazy, like org_nav."""

    def links():
        from . import board, coc, leave, saln
        from .models import CertificateRequest, PasswordResetRequest, PdsSubmission
        from .permissions import can_handle_password_resets, is_office_head

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or (is_sysadmin(user) and not user.is_superuser):
            return {"show": False}
        profile = getattr(user, "profile", None)
        person = profile.person if profile and profile.person_id else None
        approver = can_approve_leave(user)
        return {
            "show": True,
            "person": person is not None,
            "approver": approver,
            "inbox": len(leave.inbox(user)) if approver else 0,
            "register": can_view_leave_register(user),
            "plantilla": can_view_plantilla_report(user),
            "unread": board.unread_memos(person).count() if person else 0,
            "certificates": can_view_certificate_requests(user),
            "certificates_pending": CertificateRequest.objects.filter(
                status=CertificateRequest.Status.PENDING).count() if can_view_certificate_requests(user) else 0,
            "pds_waiting": PdsSubmission.objects.filter(
                status=PdsSubmission.Status.SUBMITTED).count() if can_view_certificate_requests(user) else 0,
            "certificates_ready": CertificateRequest.objects.filter(
                person=person, status=CertificateRequest.Status.READY).count() if person else 0,
            "saln": bool(person and saln.is_covered(person)),
            "coc": bool(person and coc.earns_coc(person)),
            "saln_office": is_office_head(user) and not can_view_leave_register(user),
            "password_resets": can_handle_password_resets(user),
            "password_requests": PasswordResetRequest.objects.filter(
                status=PasswordResetRequest.Status.PENDING).count() if can_handle_password_resets(user) else 0,
        }

    return {"board_nav": SimpleLazyObject(links)}


def app_version(request):
    """The release and exact build, for the footer (DECISIONS H9)."""
    return {"app_version": settings.APP_VERSION, "app_build": settings.APP_BUILD}


def mfa_nav(request):
    """True while the session still has to pass the second login step: base.html hides the menus (A5)."""
    from .mfa import needs_second_step

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not hasattr(request, "session"):
        return {"mfa_pending": False}
    return {"mfa_pending": SimpleLazyObject(lambda: needs_second_step(request))}
