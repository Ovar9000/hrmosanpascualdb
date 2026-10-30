"""The official email register, account requests and hand-over slips (DECISIONS EM1-EM9)."""

from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import official_email
from .audit import log_audit_event
from .forms import EmailAccountForm, EmailRequestForm
from .models import AuditLog, EmailAccount, EmailRequest, Engagement, Office, Person
from .official_email import EmailRegisterError
from .permissions import (
    can_approve_email_requests, can_manage_email_register, can_request_email, can_view_email_register,
    email_request_scope, get_user_office, is_hrmo,
)
from .views import _xlsx_response


def _require_view(request):
    if not can_view_email_register(request.user):
        raise PermissionDenied("The official email register is kept by the Email Admin.")


def _require_manage(request):
    if not can_manage_email_register(request.user):
        raise PermissionDenied("Only the Email Admin changes the register.")


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _base(**context):
    return {"panel": official_email.panel(), **context}


@login_required
def email_list(request):
    _require_view(request)
    show = request.GET.get("show", "active")
    rows = EmailAccount.objects.select_related("office", "holder")
    if show in EmailAccount.Status.values:
        rows = rows.filter(status=show)
    else:
        show = "all"
    kind = request.GET.get("kind", "")
    if kind in EmailAccount.Kind.values:
        rows = rows.filter(kind=kind)
    due = official_email.due()
    needs = {r["account"].pk: r["action"] for r in due}
    if request.GET.get("format") == "xlsx":
        return _xlsx_response(
            request, f"official_email_register_{timezone.localdate():%Y%m%d}.xlsx", "Email accounts",
            ["Address", "Kind", "Office", "Holder", "Date issued", "Approved by", "Approval reference",
             "Password last reset", "Status", "Closed on", "Needs"],
            [[a.address, a.get_kind_display(), a.office.name if a.office else "",
              a.holder.full_name if a.holder else "", a.issued_on, a.approved_by, a.approval_ref,
              a.password_reset_on, a.get_status_display(), a.closed_on,
              {official_email.RESET: "Reset password", official_email.CLOSE: "Close"}.get(needs.get(a.pk), "")]
             for a in rows],
            "Official email register (Excel)", model_name="EmailAccount",
        )
    manage = can_manage_email_register(request.user)
    return render(request, "hr/email_list.html", _base(
        rows=[(a, needs.get(a.pk)) for a in rows], show=show, kind=kind, kinds=EmailAccount.Kind.choices,
        counts={k: EmailAccount.objects.filter(status=k).count() for k in EmailAccount.Status.values},
        due=due, upcoming=official_email.upcoming(), manage=manage,
        missing=official_email.offices_without_account() if manage else [],
        to_make=EmailRequest.objects.filter(status=EmailRequest.Status.APPROVED).count(),
        domain=official_email.domain(), at_domain=f"@{official_email.domain()}" if official_email.domain() else "",
    ))


@login_required
def email_new(request):
    """Add an account to the register; from an approved request it starts filled in and closes the request
    (EM8)."""
    _require_manage(request)
    initial = {"issued_on": timezone.localdate()}
    req = None
    if request.GET.get("request", "").isdigit():
        req = get_object_or_404(EmailRequest.objects.select_related("office", "person", "decided_by"),
                                pk=request.GET["request"], type=EmailRequest.Type.NEW, status=EmailRequest.Status.APPROVED)
        initial.update(kind=req.kind, office=req.office_id, holder=req.person_id,
                       address=req.address or (official_email.suggested_address(req.office) if req.office else ""),
                       approved_by=official_email.approver_name(req.decided_by), approval_ref=req.number)
    elif request.GET.get("office", "").isdigit():
        office = Office.objects.filter(pk=request.GET["office"]).first()
        if office:
            initial.update(kind=EmailAccount.Kind.OFFICE, office=office.pk,
                           address=official_email.suggested_address(office))
    form = EmailAccountForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                account = form.save(commit=False)
                account.created_by = request.user
                account.save()
                if req is not None:
                    locked = EmailRequest.objects.select_for_update().get(pk=req.pk)
                    official_email.complete(locked, request.user, account)
                    locked.version += 1
                    locked.save()
        except EmailRegisterError as exc:
            messages.error(request, str(exc))
            return redirect("email_request_detail", pk=req.pk)
        messages.success(request, f"{account.address} added to the register."
                         + (" Print the hand-over slip for the holder." if account.holder_id else ""))
        return redirect("email_detail", pk=account.pk)
    return render(request, "hr/email_form.html", _base(form=form, account=None, req=req))


@login_required
def email_edit(request, pk):
    _require_manage(request)
    account = get_object_or_404(EmailAccount, pk=pk)
    form = EmailAccountForm(request.POST or None, instance=account)
    if request.method == "POST" and form.is_valid():
        if str(account.version) != request.POST.get("version", ""):
            messages.error(request, "Someone changed this account while you had it open. Check it again.")
            return redirect("email_detail", pk=pk)
        account = form.save(commit=False)
        account.version += 1
        account.save()
        messages.success(request, "Account saved.")
        return redirect("email_detail", pk=pk)
    return render(request, "hr/email_form.html", _base(form=form, account=account))


def _holder_choices(account):
    """Who can take over the account: people in active service, in the account's office for an office
    account or list."""
    engs = Engagement.objects.filter(status=Engagement.Status.ACTIVE)
    if official_email.office_scoped(account):
        engs = engs.filter(office=account.office_id)
    return list(Person.objects.filter(pk__in=engs.values("person")).order_by("last_name", "first_name"))


@login_required
def email_detail(request, pk):
    _require_view(request)
    account = get_object_or_404(EmailAccount.objects.select_related("office", "holder", "created_by"), pk=pk)
    manage = can_manage_email_register(request.user)
    today = timezone.localdate()
    if request.method == "POST":
        _require_manage(request)
        action = request.POST.get("action", "")
        try:
            with transaction.atomic():
                locked = (EmailAccount.objects.select_for_update(of=("self",)).select_related("office", "holder")
                          .get(pk=pk))
                if str(locked.version) != request.POST.get("version", ""):
                    raise EmailRegisterError("Someone changed this account while you had it open. Check it again.")
                done_on, note = _date(request.POST.get("done_on")), request.POST.get("note", "").strip()
                if action == "reset":
                    official_email.hand_over(locked, request.user, locked.holder, done_on, today, note)
                elif action == "handover":
                    raw = request.POST.get("to_holder", "")
                    to_holder = None
                    if not raw:
                        raise EmailRegisterError("Choose who holds the account now.")
                    if raw != "none":
                        to_holder = next((p for p in _holder_choices(locked) if str(p.pk) == raw), None)
                        if to_holder is None:
                            raise EmailRegisterError("Pick who holds the account now from the list.")
                    official_email.hand_over(locked, request.user, to_holder, done_on, today, note)
                elif action == "close":
                    official_email.close(locked, request.user, done_on, today, note)
                else:
                    raise EmailRegisterError("Unknown action.")
                locked.version += 1
                locked.save()
        except EmailRegisterError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, {"reset": "Password reset recorded. Print a hand-over slip if you gave out a "
                                                "new password.",
                                       "handover": "Handover recorded. Print the hand-over slip for the new holder.",
                                       "close": "Account closed."}[action])
        return redirect("email_detail", pk=pk)
    due = next((r for r in official_email.due() if r["account"].pk == account.pk), None)
    upcoming = next((r for r in official_email.upcoming() if r["account"].pk == account.pk), None)
    return render(request, "hr/email_detail.html", _base(
        account=account, manage=manage and account.is_active, today=today, due=due, upcoming=upcoming,
        history=account.handovers.select_related("from_holder", "to_holder", "recorded_by"),
        people=_holder_choices(account) if manage and account.is_active else [],
        personal=account.kind == EmailAccount.Kind.PERSONAL,
        requests=account.requests.select_related("requested_by"),
    ))


@login_required
def email_slip(request, pk):
    """The hand-over slip (EM9): the address and how to log in for the holder, a line to write the temporary
    password on by hand (never stored here), and a receipt the holder signs and the Email Admin keeps."""
    _require_manage(request)
    account = get_object_or_404(EmailAccount.objects.select_related("office", "holder"), pk=pk, status="active")
    log_audit_event(action=AuditLog.Action.EXPORT, model_name="EmailAccount", object_id=account.pk,
                    object_repr=f"Email hand-over slip (print): {account}"[:255], request=request)
    return render(request, "hr/email_slip.html", _base(
        account=account, webmail=settings.OFFICIAL_EMAIL_WEBMAIL, today=timezone.localdate(),
    ))


# --- Requests (EM6-EM8) -----------------------------------------------------------------------------


def _require_requests(request):
    user = request.user
    if not (can_request_email(user) or can_view_email_register(user) or can_approve_email_requests(user)):
        raise PermissionDenied("Office heads, Executives, HRMO and the Email Admin use email account requests.")


@login_required
def request_list(request):
    _require_requests(request)
    user = request.user
    show = request.GET.get("show", "open")
    rows = email_request_scope(user).select_related("office", "person", "account", "requested_by")
    if show == "open":
        rows = rows.filter(status__in=EmailRequest.OPEN)
    elif show in EmailRequest.Status.values:
        rows = rows.filter(status=show)
    else:
        show = "all"
    approver, manage = can_approve_email_requests(user), can_manage_email_register(user)
    return render(request, "hr/email_request_list.html", _base(
        rows=rows[:300], show=show, statuses=EmailRequest.Status.choices,
        to_approve=EmailRequest.objects.filter(status=EmailRequest.Status.PENDING).exclude(requested_by=user)
        .select_related("office", "person") if approver else [],
        to_make=EmailRequest.objects.filter(status=EmailRequest.Status.APPROVED)
        .select_related("office", "person", "account") if manage else [],
        can_request=can_request_email(user), manage=manage,
    ))


def _request_office(user):
    """None for those who ask for any office; an office head's own office otherwise (EM7)."""
    if can_manage_email_register(user) or can_approve_email_requests(user) or is_hrmo(user):
        return None
    return get_user_office(user)


@login_required
def request_new(request):
    if not can_request_email(request.user):
        raise PermissionDenied("Office heads, Executives and HRMO ask for email accounts.")
    office = _request_office(request.user)
    initial = {"type": EmailRequest.Type.NEW, "office": office.pk if office else None}
    if request.GET.get("account", "").isdigit():
        initial.update(type=EmailRequest.Type.CLOSE, account=int(request.GET["account"]))
    form = EmailRequestForm(request.POST or None, initial=initial, office=office)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                req = official_email.file_request(form.save(commit=False), request.user)
        except EmailRegisterError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Request {req.number} filed. " + (
                "It is with the Mayor's office for approval." if req.status == EmailRequest.Status.PENDING
                else "It is with the Email Admin."))
            return redirect("email_request_detail", pk=req.pk)
    return render(request, "hr/email_request_form.html", _base(form=form, office=office))


@login_required
def request_detail(request, pk):
    _require_requests(request)
    user = request.user
    req = get_object_or_404(email_request_scope(user).select_related(
        "office", "person", "account", "requested_by", "decided_by", "done_by"), pk=pk)
    approver, manage = can_approve_email_requests(user), can_manage_email_register(user)
    own = req.requested_by_id == user.pk
    today = timezone.localdate()
    if request.method == "POST":
        action = request.POST.get("action", "")
        allowed = {"approve": approver, "decline": approver, "not_done": manage, "close_done": manage,
                   "withdraw": own or manage}
        if not allowed.get(action):
            raise PermissionDenied("You cannot do that on this request.")
        try:
            with transaction.atomic():
                locked = EmailRequest.objects.select_for_update().get(pk=pk)
                if str(locked.version) != request.POST.get("version", ""):
                    raise EmailRegisterError("Someone changed this request while you had it open. Check it again.")
                note = request.POST.get("note", "").strip()
                if action in ("approve", "decline"):
                    official_email.decide(locked, user, action == "approve", note)
                elif action == "not_done":
                    official_email.not_done(locked, user, note)
                elif action == "close_done":
                    if locked.type != EmailRequest.Type.CLOSE:
                        raise EmailRegisterError("Add a new account from the register form.")
                    official_email.close_for_request(locked, user, _date(request.POST.get("done_on")), today, note)
                else:
                    official_email.withdraw(locked)
                locked.version += 1
                locked.save()
        except EmailRegisterError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, {"approve": "Approved: it is now with the Email Admin.", "decline": "Declined.",
                                       "not_done": "Marked as not done.", "close_done": "Account closed.",
                                       "withdraw": "Request withdrawn."}[action])
        return redirect("email_request_detail", pk=pk)
    return render(request, "hr/email_request_detail.html", _base(
        req=req, today=today, own=own, manage=manage,
        can_decide=approver and not own and req.status == EmailRequest.Status.PENDING,
        can_make=manage and req.status == EmailRequest.Status.APPROVED,
        can_withdraw=(own or manage) and req.is_open,
    ))
