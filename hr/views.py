import csv
import json
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from .audit import log_audit_event
from .models import AuditLog, Document, Engagement, LeaveCredit, LeaveType, Office, Person
from .permissions import (
    can_view_document_content,
    can_view_ids,
    can_view_pay,
    get_visible_offices,
    get_visible_people,
    is_auditor,
    is_hrmo,
    is_sysadmin,
)



@login_required
def office_list(request):
    """Directory of municipal offices with staff counts, scoped to user role."""
    if is_sysadmin(request.user) and not request.user.is_superuser:
        return redirect("admin:index")

    visible_offices = get_visible_offices(request.user)
    if not visible_offices.exists() and not (request.user.is_superuser or is_hrmo(request.user)):
        return HttpResponseForbidden("You do not have permission to access the offices directory.")

    query = request.GET.get("q", "").strip()
    branch = request.GET.get("branch", "").strip()
    office_type = request.GET.get("type", "").strip()

    qs = visible_offices.annotate(
        active_staff_count=Count(
            "engagements",
            filter=Q(engagements__status=Engagement.Status.ACTIVE),
            distinct=True,
        )
    ).select_related("parent")

    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(acronym__icontains=query) | Q(head_title__icontains=query))
    if branch:
        qs = qs.filter(branch=branch)
    if office_type:
        qs = qs.filter(type=office_type)

    return render(
        request,
        "hr/office_list.html",
        {
            "offices": qs,
            "query": query,
            "selected_branch": branch,
            "selected_type": office_type,
            "branches": Office.Branch.choices,
            "types": Office.Type.choices,
        },
    )


@login_required
def office_detail(request, pk):
    """Office details and staff roster, scoped to user role."""
    if is_sysadmin(request.user) and not request.user.is_superuser:
        return redirect("admin:index")

    visible_offices = get_visible_offices(request.user)
    office = get_object_or_404(visible_offices.select_related("parent"), pk=pk)

    active_engagements = (
        office.engagements.filter(status=Engagement.Status.ACTIVE)
        .select_related("person")
        .order_by("position_title", "person__last_name")
    )
    ended_engagements = (
        office.engagements.exclude(status=Engagement.Status.ACTIVE)
        .select_related("person")
        .order_by("-end_date")[:20]
    )

    return render(
        request,
        "hr/office_detail.html",
        {
            "office": office,
            "active_engagements": active_engagements,
            "ended_engagements": ended_engagements,
        },
    )


@login_required
def person_list(request):
    """Searchable, filterable list of personnel scoped to user role."""
    if is_sysadmin(request.user) and not request.user.is_superuser:
        return redirect("admin:index")

    visible_people = get_visible_people(request.user)
    if not visible_people.exists() and not (request.user.is_superuser or is_hrmo(request.user)):
        return HttpResponseForbidden("You do not have permission to view personnel records.")

    query = request.GET.get("q", "").strip()
    office_id = request.GET.get("office", "").strip()
    eng_type = request.GET.get("type", "").strip()
    status = request.GET.get("status", "").strip()

    qs = visible_people.prefetch_related(
        "engagements__office"
    ).order_by("last_name", "first_name")

    if query:
        qs = qs.filter(
            Q(last_name__icontains=query)
            | Q(first_name__icontains=query)
            | Q(employee_no__icontains=query)
            | Q(engagements__position_title__icontains=query)
        ).distinct()

    if office_id:
        qs = qs.filter(engagements__office_id=office_id).distinct()

    if eng_type:
        qs = qs.filter(engagements__type=eng_type).distinct()

    if status:
        qs = qs.filter(engagements__status=status).distinct()

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    offices = get_visible_offices(request.user).order_by("name")

    return render(
        request,
        "hr/person_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "selected_office": office_id,
            "selected_type": eng_type,
            "selected_status": status,
            "offices": offices,
            "engagement_types": Engagement.Type.choices,
            "engagement_statuses": Engagement.Status.choices,
        },
    )


@login_required
def person_detail(request, pk):
    """Person detail: profile, engagement history, documents, leave credits."""
    if is_sysadmin(request.user) and not request.user.is_superuser:
        return redirect("admin:index")

    visible_people = get_visible_people(request.user)
    person = get_object_or_404(
        visible_people.prefetch_related("engagements__office", "engagements__project", "documents"),
        pk=pk,
    )

    # Pay rate fields visible ONLY to HRMO and auditors (DECISIONS R6).
    can_view_rates = can_view_pay(request.user)

    # Government IDs visible to HRMO, Auditor, or self (DECISIONS R4)
    can_view_id_numbers = can_view_ids(request.user, person)

    # Document file content inspection / download
    can_view_doc_content = can_view_document_content(request.user, person=person)

    # Calculate leave credits summary if person earns them
    leave_balances = {}
    if person.earns_leave_credits():
        for lt_code, lt_label in LeaveType.choices:
            total = (
                LeaveCredit.objects.filter(person=person, leave_type=lt_code)
                .aggregate(total=Sum("days"))["total"]
                or 0
            )
            if total > 0:
                leave_balances[lt_label] = total

    engagements = person.engagements.all().order_by("-start_date")
    documents = person.documents.all().order_by("-uploaded_at")

    # Audit log VIEW event for person detail (DECISIONS U1)
    log_audit_event(
        action=AuditLog.Action.VIEW,
        model_name="Person",
        object_id=person.pk,
        object_repr=str(person),
        request=request,
    )

    return render(
        request,
        "hr/person_detail.html",
        {
            "person": person,
            "engagements": engagements,
            "documents": documents,
            "can_view_rates": can_view_rates,
            "can_view_id_numbers": can_view_id_numbers,
            "can_view_doc_content": can_view_doc_content,
            "leave_balances": leave_balances,
        },
    )


@login_required
def document_download(request, pk):
    """Private file serving for uploaded documents, checking permissions."""
    doc = get_object_or_404(Document, pk=pk)
    if not can_view_document_content(request.user, doc):
        return HttpResponseForbidden("You do not have permission to view or download this document.")

    if not doc.file:
        raise Http404("Document scan is not uploaded.")

    # Audit log VIEW event for document download (DECISIONS U1)
    log_audit_event(
        action=AuditLog.Action.VIEW,
        model_name="Document",
        object_id=doc.pk,
        object_repr=str(doc),
        request=request,
    )

    return FileResponse(doc.file.open(), as_attachment=False, filename=doc.file.name.split("/")[-1])


@login_required
def audit_list(request):
    """Auditor-only searchable audit trail (DECISIONS U1-U6)."""
    if not (request.user.is_superuser or is_auditor(request.user) or request.user.has_perm("hr.view_audit_log")):
        return HttpResponseForbidden("You do not have permission to access system audit logs.")

    qs = AuditLog.objects.all().order_by("-timestamp")
    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()
    user_filter = request.GET.get("user", "").strip()

    if query:
        qs = qs.filter(
            Q(username__icontains=query)
            | Q(object_repr__icontains=query)
            | Q(model_name__icontains=query)
            | Q(object_id__icontains=query)
        )
    if action:
        qs = qs.filter(action=action)
    if user_filter:
        qs = qs.filter(username__icontains=user_filter)
    if start_date:
        qs = qs.filter(timestamp__date__gte=start_date)
    if end_date:
        qs = qs.filter(timestamp__date__lte=end_date)

    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        "hr/audit_list.html",
        {
            "page_obj": page_obj,
            "query": query,
            "selected_action": action,
            "selected_user": user_filter,
            "start_date": start_date,
            "end_date": end_date,
            "actions": AuditLog.Action.choices,
        },
    )


@login_required
def audit_export_csv(request):
    """CSV export of audit log records, restricted to Auditor (DECISIONS U3)."""
    if not (request.user.is_superuser or is_auditor(request.user) or request.user.has_perm("hr.view_audit_log")):
        return HttpResponseForbidden("You do not have permission to export system audit logs.")

    # Audit log EXPORT event (DECISIONS U1)
    log_audit_event(
        action=AuditLog.Action.EXPORT,
        model_name="AuditLog",
        object_repr="Audit log CSV export",
        request=request,
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sanpascual_audit_log.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Timestamp", "Action", "User ID", "Username", "Office",
        "Model", "Object ID", "Object Description", "Changes", "IP Address"
    ])

    qs = AuditLog.objects.all().order_by("-timestamp")[:5000]
    for row in qs:
        writer.writerow([
            row.timestamp.isoformat(),
            row.action,
            row.user_id or "",
            row.username or "",
            row.office_name or "",
            row.model_name,
            row.object_id,
            row.object_repr,
            json.dumps(row.changes),
            row.ip_address or "",
        ])

    return response

