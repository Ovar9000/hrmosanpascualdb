"""The official email register screens (DECISIONS EM1-EM5)."""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import official_email
from .forms import EmailAccountForm
from .models import EmailAccount, Engagement, Office, Person
from .official_email import EmailRegisterError
from .permissions import can_manage_email_register, can_view_email_register, is_self
from .views import _xlsx_response


def _require_view(request):
    if not can_view_email_register(request.user):
        raise PermissionDenied("The official email register is kept by HRMO.")


def _manage(request, account=None):
    """HRMO, except on an account they hold themselves (R10)."""
    if not can_manage_email_register(request.user):
        return False
    return account is None or account.holder is None or not is_self(request.user, account.holder)


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


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
    manage = _manage(request)
    return render(request, "hr/email_list.html", {
        "rows": [(a, needs.get(a.pk)) for a in rows], "show": show, "kind": kind, "kinds": EmailAccount.Kind.choices,
        "counts": {k: EmailAccount.objects.filter(status=k).count() for k in EmailAccount.Status.values},
        "due": due, "upcoming": official_email.upcoming(), "manage": manage,
        "missing": official_email.offices_without_account() if manage else [],
        "domain": official_email.domain(), "at_domain": f"@{official_email.domain()}" if official_email.domain() else "",
    })


@login_required
def email_new(request):
    _require_view(request)
    if not _manage(request):
        raise PermissionDenied("Only HRMO adds accounts to the register.")
    initial = {"issued_on": timezone.localdate()}
    office = Office.objects.filter(pk=request.GET.get("office")).first() if request.GET.get("office", "").isdigit() else None
    if office:
        initial.update(kind=EmailAccount.Kind.OFFICE, office=office.pk,
                       address=official_email.suggested_address(office))
    form = EmailAccountForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        holder = form.cleaned_data.get("holder")
        if holder and is_self(request.user, holder):
            form.add_error("holder", "Another HRMO staff member records an account you hold.")
        else:
            account = form.save(commit=False)
            account.created_by = request.user
            account.save()
            messages.success(request, f"{account.address} added to the register.")
            return redirect("email_detail", pk=account.pk)
    return render(request, "hr/email_form.html", {"form": form, "account": None})


@login_required
def email_edit(request, pk):
    _require_view(request)
    account = get_object_or_404(EmailAccount, pk=pk)
    if not _manage(request, account):
        raise PermissionDenied("Only HRMO edits the register, and never an account they hold.")
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
    return render(request, "hr/email_form.html", {"form": form, "account": account})


def _holder_choices(request, account):
    """Who can take over the account: people in active service, in the account's office for an office
    account or list; never the user themselves (R10)."""
    engs = Engagement.objects.filter(status=Engagement.Status.ACTIVE)
    if official_email.office_scoped(account):
        engs = engs.filter(office=account.office_id)
    people = Person.objects.filter(pk__in=engs.values("person")).order_by("last_name", "first_name")
    return [p for p in people if not is_self(request.user, p)]


@login_required
def email_detail(request, pk):
    _require_view(request)
    account = get_object_or_404(EmailAccount.objects.select_related("office", "holder", "created_by"), pk=pk)
    manage = _manage(request, account)
    today = timezone.localdate()
    if request.method == "POST":
        if not manage:
            raise PermissionDenied("Only HRMO records resets and closings, and never on an account they hold.")
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
                        to_holder = next((p for p in _holder_choices(request, locked) if str(p.pk) == raw), None)
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
            messages.success(request, {"reset": "Password reset recorded.", "handover": "Handover recorded.",
                                       "close": "Account closed."}[action])
        return redirect("email_detail", pk=pk)
    due = next((r for r in official_email.due() if r["account"].pk == account.pk), None)
    upcoming = next((r for r in official_email.upcoming() if r["account"].pk == account.pk), None)
    return render(request, "hr/email_detail.html", {
        "account": account, "manage": manage and account.is_active, "today": today, "due": due, "upcoming": upcoming,
        "history": account.handovers.select_related("from_holder", "to_holder", "recorded_by"),
        "people": _holder_choices(request, account) if manage and account.is_active else [],
        "personal": account.kind == EmailAccount.Kind.PERSONAL,
    })
