"""Separation and clearance screens (DECISIONS SE1-SE5)."""

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import papers, separations
from .audit import log_audit_event
from .forms import SeparationForm
from .models import AuditLog, ClearanceItem, Person, Separation
from .permissions import can_manage_record_of, can_manage_separations, can_view_separations


def _require_view(request):
    if not can_view_separations(request.user):
        raise PermissionDenied("Separations and clearances are kept by HRMO.")


def _manage(request, person):
    return can_manage_separations(request.user) and can_manage_record_of(request.user, person)


@login_required
def separation_list(request):
    _require_view(request)
    show = request.GET.get("show", "clearing")
    rows = Separation.objects.select_related("person", "engagement__office").prefetch_related("clearance_items")
    if show in Separation.Status.values:
        rows = rows.filter(status=show)
    else:
        show = "all"
    rows = rows.order_by("last_day") if show == "clearing" else rows.order_by("-last_day")
    return render(request, "hr/separation_list.html", {
        "rows": [(s, separations.outstanding(s)) for s in rows[:300]], "show": show,
        "counts": {k: Separation.objects.filter(status=k).count() for k in Separation.Status.values},
        "today": timezone.localdate(),
    })


@login_required
def separation_new(request, person_pk):
    _require_view(request)
    person = get_object_or_404(Person, pk=person_pk)
    if not _manage(request, person):
        raise PermissionDenied("Only HRMO starts a separation, and never their own.")
    form = SeparationForm(request.POST or None, person=person)
    if not form.fields["engagement"].queryset.exists():
        messages.error(request, f"{person.full_name} has no active appointment or contract.")
        return redirect("person_detail", pk=person.pk)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                sep = separations.start(form.save(commit=False), request.user)
        except IntegrityError:
            form.add_error("engagement", "A separation is already in progress for this appointment.")
        else:
            messages.success(request, "Separation started. Record each clearance signature as it comes back.")
            return redirect("separation_detail", pk=sep.pk)
    return render(request, "hr/separation_form.html", {"form": form, "person": person})


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


@login_required
def separation_detail(request, pk):
    _require_view(request)
    sep = get_object_or_404(Separation.objects.select_related("person", "engagement__office", "created_by",
                                                              "completed_by"), pk=pk)
    manage = _manage(request, sep.person)
    if request.method == "POST":
        if not manage:
            raise PermissionDenied("Only HRMO records clearances, and never their own.")
        action = request.POST.get("action", "")
        try:
            with transaction.atomic():
                locked = Separation.objects.select_for_update().get(pk=pk)
                if str(locked.version) != request.POST.get("version", ""):
                    raise separations.SeparationError("Someone changed this separation while you had it open. Check it again.")
                if action == "item":
                    item = get_object_or_404(ClearanceItem, pk=request.POST.get("item"), separation=locked)
                    separations.record_item(item, request.user, request.POST.get("status", ""),
                                            request.POST.get("signed_by_name", "").strip(),
                                            _date(request.POST.get("signed_on")), request.POST.get("remarks", "").strip())
                elif action == "complete":
                    separations.complete(locked, request.user)
                elif action == "cancel":
                    separations.cancel(locked)
                else:
                    raise separations.SeparationError("Unknown action.")
                locked.version += 1
                locked.save()
        except separations.SeparationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, {"item": "Clearance line saved.", "cancel": "Separation cancelled.",
                                       "complete": "Separation completed: the appointment is ended."}[action])
        return redirect("separation_detail", pk=pk)
    items = list(sep.clearance_items.select_related("recorded_by"))
    return render(request, "hr/separation_detail.html", {
        "sep": sep, "items": items, "manage": manage and sep.is_open,
        "outstanding": [i for i in items if i.status not in separations.DONE_STATUSES],
        "statuses": ClearanceItem.Status.choices, "terminal": separations.terminal_leave(sep),
        "today": timezone.localdate(),
    })


@login_required
def separation_print(request, pk):
    """The clearance form, filled in with what is recorded, for the signatories' wet signatures (SE2)."""
    _require_view(request)
    sep = get_object_or_404(Separation.objects.select_related("person", "engagement__office"), pk=pk)
    log_audit_event(action=AuditLog.Action.EXPORT, model_name="Separation", object_id=sep.pk,
                    object_repr=f"Clearance form (print): {sep}"[:255], request=request)
    return render(request, "hr/clearance_print.html", {
        "sep": sep, "items": sep.clearance_items.all(), "sign": papers.signatories(), "today": timezone.localdate(),
    })
