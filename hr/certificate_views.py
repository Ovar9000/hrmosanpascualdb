"""Screens for service records and certificates of employment: the person's own requests, HRMO's
desk, and the printed documents (DECISIONS CR1-CR6)."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import certificates
from .audit import log_audit_event
from .forms import CertificateRecordForm, CertificateRequestForm
from .models import AuditLog, CertificateRequest, Person
from .permissions import can_manage_certificate_requests, can_view_certificate_requests, is_self
from .sequences import get_next_control_no

Req = CertificateRequest


def _own_person(user):
    profile = getattr(user, "profile", None)
    return profile.person if profile and profile.person_id else None


def _create(form, person, user, **extra):
    saved = form.save(commit=False)
    saved.person = person
    saved.filed_by = user
    for key, value in extra.items():
        setattr(saved, key, value)
    saved.control_no = get_next_control_no(timezone.localdate().year)
    saved.save()
    return saved


@login_required
def my_certificates(request):
    """The person's own requests, open ones first. Works after separation too: the account is
    linked to the personnel record, not to an active appointment (CR2)."""
    person = _own_person(request.user)
    if person is None:
        if can_view_certificate_requests(request.user):
            return redirect("certificate_list")
        raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")
    rows = person.certificate_requests.order_by("-filed_at")
    return render(request, "hr/certificate_mine.html", {
        "person": person,
        "open": [r for r in rows if r.is_open],
        "page": Paginator([r for r in rows if not r.is_open], 20).get_page(request.GET.get("page")),
        "has_service_record": certificates.has_service_record(person),
    })


@login_required
def certificate_new(request):
    """The person asks for a service record or certificate online (CR2)."""
    person = _own_person(request.user)
    if person is None:
        raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")
    if request.method == "POST":
        form = CertificateRequestForm(request.POST, person=person)
        if certificates.open_count(person) >= certificates.MAX_OPEN_REQUESTS:
            form.add_error(None, f"You already have {certificates.MAX_OPEN_REQUESTS} requests in progress. "
                                 "Wait for one to be released, or cancel one you no longer need.")
        elif form.is_valid():
            saved = _create(form, person, request.user, received_via=Req.Via.ONLINE, requester=Req.Requester.SELF)
            messages.success(request, f"Request {saved.control_no} sent to HRMO. You will see here when it is ready.")
            return redirect("certificate_detail", pk=saved.pk)
    else:
        form = CertificateRequestForm(person=person, initial={
            "contact": person.mobile or person.email,
            "kind": Req.Kind.SERVICE_RECORD if certificates.has_service_record(person) else Req.Kind.COE,
        })
    return render(request, "hr/certificate_form.html", {"form": form, "person": person, "for_self": True})


@login_required
def certificate_record(request, person_pk):
    """HRMO records a request that came in at the counter, by letter or by email, for anyone on
    record: in service, separated, or deceased and asked for by heirs (CR2)."""
    if not can_manage_certificate_requests(request.user):
        raise PermissionDenied("Only HRMO records certificate requests.")
    person = get_object_or_404(Person, pk=person_pk)
    if request.method == "POST":
        form = CertificateRecordForm(request.POST, person=person)
        if form.is_valid():
            saved = _create(form, person, request.user)
            messages.success(request, f"Recorded {saved.control_no}.")
            return redirect("certificate_detail", pk=saved.pk)
    else:
        form = CertificateRecordForm(person=person, initial={
            "received_via": Req.Via.WALK_IN, "contact": person.mobile or person.email,
            "kind": Req.Kind.SERVICE_RECORD if certificates.has_service_record(person) else Req.Kind.COE,
        })
    return render(request, "hr/certificate_form.html", {"form": form, "person": person, "for_self": False})


@login_required
def certificate_list(request):
    """HRMO's desk: requests to prepare, ready for release, and the history (CR3)."""
    if not can_view_certificate_requests(request.user):
        raise PermissionDenied("Certificate requests are kept by HRMO.")
    show = request.GET.get("show", "open")
    rows = Req.objects.select_related("person").order_by("filed_at")
    if show == "open":
        rows = rows.filter(status=Req.Status.PENDING)
    elif show == "ready":
        rows = rows.filter(status=Req.Status.READY)
    else:
        show = "all"
        rows = rows.order_by("-filed_at")
    query = request.GET.get("q", "").strip()
    if query:
        rows = rows.filter(Q(control_no__icontains=query) | Q(person__last_name__icontains=query)
                           | Q(person__first_name__icontains=query) | Q(person__employee_no__icontains=query)
                           | Q(requester_name__icontains=query))
    return render(request, "hr/certificate_list.html", {
        "page": Paginator(rows, 25).get_page(request.GET.get("page")),
        "show": show, "q": query,
        "counts": {
            "open": Req.objects.filter(status=Req.Status.PENDING).count(),
            "ready": Req.objects.filter(status=Req.Status.READY).count(),
        },
        "can_manage": can_manage_certificate_requests(request.user),
        "today": timezone.localdate(),
    })


def _visible_request(request, pk):
    req = get_object_or_404(Req.objects.select_related("person", "filed_by", "prepared_by"), pk=pk)
    if not (can_view_certificate_requests(request.user) or is_self(request.user, req.person)):
        raise PermissionDenied("You cannot see this request.")
    return req


@login_required
def certificate_detail(request, pk):
    """One request: where it is, and HRMO's prepare / release / decline buttons (CR3). The person
    can cancel it while it is still being prepared."""
    req = _visible_request(request, pk)
    manage = can_manage_certificate_requests(request.user)
    if request.method == "POST":
        action = request.POST.get("action", "")
        own = is_self(request.user, req.person) and req.filed_by_id == request.user.pk
        allowed = {"ready", "release", "decline", "reopen"} if manage else set()
        if own:
            allowed.add("cancel")
        if action not in allowed:
            raise PermissionDenied("You cannot do that to this request.")
        note = request.POST.get("note", "").strip()
        released_to = request.POST.get("released_to", "").strip()
        error = None
        with transaction.atomic():
            locked = Req.objects.select_for_update().get(pk=req.pk)
            if str(locked.version) != request.POST.get("version", ""):
                error = "Someone changed this request while you had it open. Check it again."
            elif action == "ready" and locked.status == Req.Status.PENDING:
                locked.status, locked.prepared_at, locked.prepared_by = Req.Status.READY, timezone.now(), request.user
            elif action == "release" and locked.status == Req.Status.READY:
                if not released_to:
                    error = "Write who received the document."
                else:
                    locked.status, locked.released_at, locked.released_to = Req.Status.RELEASED, timezone.now(), released_to[:200]
                    locked.note = note
            elif action == "decline" and locked.is_open:
                if not note:
                    error = "Give the reason for declining, so the person knows what to do."
                else:
                    locked.status, locked.note = Req.Status.DECLINED, note
            elif action == "cancel" and locked.status == Req.Status.PENDING:
                locked.status = Req.Status.CANCELLED
            elif action == "reopen" and locked.status == Req.Status.READY:
                locked.status, locked.prepared_at, locked.prepared_by = Req.Status.PENDING, None, None
            else:
                error = "This request has moved on since you opened it."
            if error is None:
                locked.version += 1
                locked.save()
        if error:
            messages.error(request, error)
        else:
            messages.success(request, {
                "ready": "Marked ready for release.", "release": "Released.", "decline": "Declined.",
                "cancel": "Request cancelled.", "reopen": "Back to being prepared.",
            }[action])
        return redirect("certificate_detail", pk=pk)
    return render(request, "hr/certificate_detail.html", {
        "req": req,
        "can_manage": manage,
        "can_print": can_view_certificate_requests(request.user),
        "can_cancel": is_self(request.user, req.person) and req.filed_by_id == request.user.pk
                      and req.status == Req.Status.PENDING,
        "has_service_record": certificates.has_service_record(req.person),
    })


def _document(request, person, kind, req=None):
    """Render the service record or certificate. Without a request it is a preview for checking,
    marked as not for release and without a control number (CR3)."""
    today = timezone.localdate()
    context = {
        "person": person, "req": req, "preview": req is None, "today": today,
        "day": certificates.ordinal_day(today.day),
        "signatory": settings.CERTIFICATE_SIGNATORY, "signatory_title": settings.CERTIFICATE_SIGNATORY_TITLE,
        "requested_by": (req.requester_name if req and req.requester_name else None),
        "autoprint": request.GET.get("autoprint") == "1",
    }
    if kind == Req.Kind.SERVICE_RECORD:
        rows = certificates.service_rows(person)
        context.update(rows=rows, salary_missing=any(r["salary_missing"] for r in rows))
        template = "hr/service_record_print.html"
    else:
        context.update(certificates.coe_context(person, with_pay=kind == Req.Kind.COE_PAY), with_pay=kind == Req.Kind.COE_PAY)
        template = "hr/coe_print.html"
    log_audit_event(action=AuditLog.Action.EXPORT, model_name="Person", object_id=person.pk,
                    object_repr=f"{Req.Kind(kind).label} {'preview' if req is None else req.control_no}: {person}"[:255],
                    request=request)
    return render(request, template, context)


@login_required
def certificate_print(request, pk):
    """The document for a request, to print, sign and seal. HRMO and Auditor only: it shows salaries
    (R6), and an unsigned printout is not the document (F1)."""
    req = _visible_request(request, pk)
    if not can_view_certificate_requests(request.user):
        raise PermissionDenied("HRMO prints the document; you will be told when it is ready for release.")
    return _document(request, req.person, req.kind, req)


@login_required
def person_document_preview(request, pk, kind):
    """Preview a person's service record or certificate from their record before anyone asks (CR3)."""
    if not can_view_certificate_requests(request.user):
        raise PermissionDenied("Service records and certificates are prepared by HRMO.")
    if kind not in Req.Kind.values:
        raise PermissionDenied("Unknown document.")
    return _document(request, get_object_or_404(Person, pk=pk), kind)
