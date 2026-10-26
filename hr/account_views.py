"""Forgotten passwords: the login page's "Forgot password?" form and HRMO's queue (DECISIONS A2).

There is no email reset. The person asks online or at the counter; HRMO checks a photo ID and
issues a one-time password, shown once, which must be changed at first login.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from .accounts import dismiss_reset_request, issue_reset_password, record_forgot_password
from .audit import get_client_ip
from .forms import DismissResetRequestForm, ForgotPasswordForm, IssueResetPasswordForm
from .models import PasswordResetRequest, Person
from .permissions import can_handle_password_resets, get_visible_people, password_reset_blocker

Req = PasswordResetRequest


def forgot_password(request):
    """Anyone, not logged in. The answer is the same whether or not the username exists."""
    if request.user.is_authenticated:
        return redirect("password_change")
    form = ForgotPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            record_forgot_password(form.cleaned_data["username"], form.cleaned_data["contact"], get_client_ip(request))
        return redirect("forgot_password_sent")
    return render(request, "registration/forgot_password.html", {"form": form})


def forgot_password_sent(request):
    return render(request, "registration/forgot_password_sent.html")


def _require_hrmo(request):
    if not can_handle_password_resets(request.user):
        raise PermissionDenied("Only HRMO handles forgotten passwords.")


def _person_of(user):
    return Person.objects.filter(user_profile__user=user).first() if user else None


def _issued(request, user, person, password):
    """The one time the password is ever shown. Not cached, so Back does not bring it up again."""
    response = render(request, "hr/password_issued.html", {
        "account": user, "person": person, "password": password,
        "valid_until": user.profile.password_expires_at,
    })
    response["Cache-Control"] = "no-store"
    return response


@login_required
def reset_request_list(request):
    _require_hrmo(request)
    show = "done" if request.GET.get("show") == "done" else "waiting"
    waiting = Req.objects.filter(status=Req.Status.PENDING)
    rows = waiting.order_by("requested_at") if show == "waiting" else Req.objects.exclude(status=Req.Status.PENDING)
    page = Paginator(rows.select_related("user", "handled_by"), 25).get_page(request.GET.get("page"))
    for req in page:
        req.person = _person_of(req.user)
        req.blocker = password_reset_blocker(request.user, req.user) if req.status == Req.Status.PENDING else None
    return render(request, "hr/password_requests.html", {"page": page, "show": show, "waiting": waiting.count()})


@login_required
def reset_request_detail(request, pk):
    _require_hrmo(request)
    req = get_object_or_404(Req.objects.select_related("user", "handled_by"), pk=pk)
    person = _person_of(req.user)
    blocker = password_reset_blocker(request.user, req.user)
    issue_form = IssueResetPasswordForm(request.POST if request.POST.get("action") == "issue" else None)
    dismiss_form = DismissResetRequestForm(request.POST if request.POST.get("action") == "dismiss" else None)
    if request.method == "POST":
        if req.status != Req.Status.PENDING:
            messages.info(request, "This request was already handled.")
            return redirect("reset_request_detail", pk=req.pk)
        if request.POST.get("action") == "dismiss" and dismiss_form.is_valid():
            with transaction.atomic():
                dismiss_reset_request(req, request.user, dismiss_form.cleaned_data["note"])
            messages.success(request, "Request dismissed.")
            return redirect("reset_request_list")
        if request.POST.get("action") == "issue" and blocker is None and issue_form.is_valid():
            with transaction.atomic():
                password = issue_reset_password(req.user, request.user, issue_form.cleaned_data["id_checked"], req)
            return _issued(request, req.user, person, password)
    return render(request, "hr/password_request_detail.html", {
        "req": req, "person": person, "blocker": blocker, "issue_form": issue_form, "dismiss_form": dismiss_form,
        "history": req.user.password_reset_requests.exclude(pk=req.pk)[:5] if req.user else [],
        "reset_hours": settings.RESET_PASSWORD_HOURS,
    })


@login_required
def person_password_reset(request, pk):
    """A walk-in at the counter, from the person's record: no online request needed."""
    _require_hrmo(request)
    person = get_object_or_404(get_visible_people(request.user).select_related("user_profile__user"), pk=pk)
    profile = getattr(person, "user_profile", None)
    account = profile.user if profile else None
    if account is None:
        messages.info(request, f"{person.full_name} has no account yet. Accounts are created with provision_accounts.")
        return redirect("person_detail", pk=person.pk)
    blocker = password_reset_blocker(request.user, account)
    form = IssueResetPasswordForm(request.POST or None)
    if request.method == "POST" and blocker is None and form.is_valid():
        with transaction.atomic():
            password = issue_reset_password(account, request.user, form.cleaned_data["id_checked"])
        return _issued(request, account, person, password)
    return render(request, "hr/password_reset_person.html", {
        "person": person, "account": account, "blocker": blocker, "form": form,
        "pending": account.password_reset_requests.filter(status=Req.Status.PENDING).first(),
        "reset_hours": settings.RESET_PASSWORD_HOURS,
    })
