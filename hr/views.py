import csv
import json
import os
from datetime import date, timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from .audit import log_audit_event, run_and_record_verification
from .forms import (
    DocumentEditForm,
    DocumentForm,
    DocumentReplaceForm,
    EndEngagementForm,
    EngagementForm,
    PersonForm,
    ProjectForm,
    sanitize_filename,
)
from .models import (
    AuditChainStatus,
    AuditLog,
    Document,
    Engagement,
    LeaveCredit,
    LeaveType,
    Office,
    Person,
    Project,
    UserProfile,
)
from .permissions import (
    can_manage_hr_records,
    can_manage_projects,
    can_view_contacts,
    can_view_document_content,
    can_view_document_list,
    can_view_hr_notes,
    can_view_ids,
    can_view_pay,
    can_view_personal_details,
    can_approve_leave,
    can_view_project_roster,
    can_view_projects,
    get_visible_offices,
    get_visible_people,
    has_all_office_scope,
    is_auditor,
    is_hrmo,
    is_sysadmin,
)
from .sequences import get_next_employee_no


class PasswordChangeView(auth_views.PasswordChangeView):
    """Django's password change, plus clearing must_change_password on success (DECISIONS A1)."""

    def form_valid(self, form):
        response = super().form_valid(form)
        profile = UserProfile.objects.filter(user=self.request.user, must_change_password=True).first()
        if profile:
            profile.must_change_password = False
            profile.save()
        return response


@login_required
def home(request):
    """Landing page: Project Maintainers with no personnel access go to Projects."""
    if can_manage_projects(request.user) and not get_visible_people(request.user).exists():
        return redirect("project_list")
    return redirect("person_list")


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
    # Past staff are hidden from office-scoped roles: transferred people are not visible
    # to their former office (DECISIONS R5).
    ended_engagements = Engagement.objects.none()
    if has_all_office_scope(request.user):
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
            "can_view_rates": can_view_pay(request.user),
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
    # Ignore malformed filter values instead of failing with a 500.
    if not office_id.isdigit():
        office_id = ""
    if eng_type not in Engagement.Type.values:
        eng_type = ""
    if status not in Engagement.Status.values:
        status = ""

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
            "can_manage_records": can_manage_hr_records(request.user),
            "can_view_contacts": can_view_contacts(request.user),
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

    # Leave balances: HRMO, Auditor, self, and leave approvers (Office Head, Executive, Delegate)
    can_view_leave = can_view_personal_details(request.user, person) or can_approve_leave(request.user)

    # Calculate leave credits summary if person earns them
    leave_balances = {}
    if can_view_leave and person.earns_leave_credits():
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
            "can_view_doc_list": can_view_document_list(request.user, person),
            "can_view_personal": can_view_personal_details(request.user, person),
            "can_view_contacts": can_view_contacts(request.user, person),
            "can_view_notes": can_view_hr_notes(request.user),
            "leave_balances": leave_balances,
            "can_manage_records": can_manage_hr_records(request.user),
        },
    )


@login_required
def person_create(request):
    """Data entry view for creating a new Person record (HRMO Staff only)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to create personnel records.")

    if request.method == "POST":
        form = PersonForm(request.POST, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                emp_no = get_next_employee_no()
                person = form.save(commit=False)
                person.employee_no = emp_no
                person.version = 1
                person.save()

            messages.success(
                request,
                f"Personnel record for {person.full_name} ({person.employee_no}) successfully created."
            )
            return redirect("person_detail", pk=person.pk)
    else:
        form = PersonForm(user=request.user)

    return render(
        request,
        "hr/person_form.html",
        {
            "form": form,
            "is_edit": False,
        },
    )


@login_required
def person_edit(request, pk):
    """Edit view for an existing Person record with lost-update protection (HRMO Staff only)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to edit personnel records.")

    person = get_object_or_404(Person, pk=pk)

    if request.method == "POST":
        form = PersonForm(request.POST, instance=person, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                locked_person = Person.objects.select_for_update().get(pk=pk)
                submitted_version = form.cleaned_data.get("version")
                if locked_person.version != submitted_version:
                    form.add_error(
                        None,
                        "This record was modified by another user while you were editing it. "
                        "Please refresh the page and review the changes before saving."
                    )
                    return render(
                        request,
                        "hr/person_form.html",
                        {
                            "form": form,
                            "person": person,
                            "is_edit": True,
                        },
                    )

                saved_person = form.save(commit=False)
                saved_person.version = locked_person.version + 1
                saved_person.save()

            messages.success(
                request,
                f"Personal details for {saved_person.full_name} ({saved_person.employee_no}) successfully updated."
            )
            return redirect("person_detail", pk=saved_person.pk)
    else:
        form = PersonForm(instance=person, user=request.user)

    return render(
        request,
        "hr/person_form.html",
        {
            "form": form,
            "person": person,
            "is_edit": True,
        },
    )


@login_required
def engagement_create(request, person_pk):
    """Add a new engagement (appointment / contract) for a person (HRMO only)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to manage appointments.")

    person = get_object_or_404(Person, pk=person_pk)

    if request.method == "POST":
        form = EngagementForm(request.POST, user=request.user, person=person)
        if form.is_valid():
            with transaction.atomic():
                eng = form.save(commit=False)
                eng.person = person
                eng.version = 1
                eng.save()

            messages.success(
                request,
                f"New {eng.get_type_display()} engagement for {person.full_name} successfully recorded."
            )
            return redirect("person_detail", pk=person.pk)
    else:
        form = EngagementForm(user=request.user, person=person)

    return render(
        request,
        "hr/engagement_form.html",
        {
            "form": form,
            "person": person,
            "is_edit": False,
        },
    )


@login_required
def engagement_edit(request, pk):
    """Edit an existing engagement record with lost-update protection (HRMO only)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to edit appointments.")

    eng = get_object_or_404(Engagement.objects.select_related("person", "office"), pk=pk)

    if request.method == "POST":
        form = EngagementForm(request.POST, instance=eng, user=request.user, person=eng.person)
        if form.is_valid():
            with transaction.atomic():
                locked_eng = Engagement.objects.select_for_update().get(pk=pk)
                submitted_version = form.cleaned_data.get("version")
                if locked_eng.version != submitted_version:
                    form.add_error(
                        None,
                        "This engagement was modified by another user while you were editing it. "
                        "Please refresh the page and review before saving."
                    )
                    return render(
                        request,
                        "hr/engagement_form.html",
                        {
                            "form": form,
                            "person": eng.person,
                            "engagement": eng,
                            "is_edit": True,
                        },
                    )

                saved_eng = form.save(commit=False)
                saved_eng.version = locked_eng.version + 1
                saved_eng.save()

            messages.success(
                request,
                f"Engagement ({saved_eng.get_type_display()}) for {eng.person.full_name} successfully updated."
            )
            return redirect("person_detail", pk=eng.person.pk)
    else:
        form = EngagementForm(instance=eng, user=request.user, person=eng.person)

    return render(
        request,
        "hr/engagement_form.html",
        {
            "form": form,
            "person": eng.person,
            "engagement": eng,
            "is_edit": True,
        },
    )


@login_required
def engagement_end(request, pk):
    """End / retire an engagement (sets status=ended and end_date, keeps row intact)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to end appointments.")

    eng = get_object_or_404(Engagement.objects.select_related("person", "office"), pk=pk)

    if request.method == "POST":
        form = EndEngagementForm(request.POST, engagement=eng)
        if form.is_valid():
            with transaction.atomic():
                locked_eng = Engagement.objects.select_for_update().get(pk=pk)
                submitted_version = form.cleaned_data.get("version")
                if locked_eng.version != submitted_version:
                    form.add_error(
                        None,
                        "This engagement was modified by another user while you were viewing it. "
                        "Please refresh and try again."
                    )
                    return render(
                        request,
                        "hr/engagement_end_confirm.html",
                        {"form": form, "engagement": eng, "person": eng.person},
                    )

                locked_eng.status = Engagement.Status.ENDED
                locked_eng.end_date = form.cleaned_data["end_date"]
                if form.cleaned_data.get("notes"):
                    note_entry = f"Ended on {locked_eng.end_date}: {form.cleaned_data['notes']}"
                    locked_eng.notes = f"{locked_eng.notes}\n{note_entry}".strip()
                locked_eng.version += 1
                locked_eng.save()

            messages.success(
                request,
                f"{eng.get_type_display()} engagement for {eng.person.full_name} ended as of {locked_eng.end_date}."
            )
            return redirect("person_detail", pk=eng.person.pk)
    else:
        form = EndEngagementForm(engagement=eng)

    return render(
        request,
        "hr/engagement_end_confirm.html",
        {
            "form": form,
            "engagement": eng,
            "person": eng.person,
        },
    )


@login_required
def engagement_renew(request, pk):
    """Renew / new appointment pre-filled from previous engagement (preserves history)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to renew appointments.")

    old_eng = get_object_or_404(Engagement.objects.select_related("person", "office"), pk=pk)

    if request.method == "POST":
        form = EngagementForm(request.POST, user=request.user, person=old_eng.person)
        if form.is_valid():
            with transaction.atomic():
                new_eng = form.save(commit=False)
                new_eng.person = old_eng.person
                new_eng.version = 1
                new_eng.save()

            messages.success(
                request,
                f"New {new_eng.get_type_display()} appointment for {old_eng.person.full_name} recorded (previous history preserved)."
            )
            return redirect("person_detail", pk=old_eng.person.pk)
    else:
        start_date = (old_eng.end_date + timedelta(days=1)) if old_eng.end_date else date.today()
        initial_data = {
            "office": old_eng.office_id,
            "type": old_eng.type,
            "position_title": old_eng.position_title,
            "rate": old_eng.rate,
            "rate_basis": old_eng.rate_basis,
            "leave_eligible": old_eng.leave_eligible,
            "salary_grade": old_eng.salary_grade,
            "step": old_eng.step,
            "item_number": old_eng.item_number,
            "project": old_eng.project_id,
            "start_date": start_date,
            "status": Engagement.Status.ACTIVE,
        }
        form = EngagementForm(initial=initial_data, user=request.user, person=old_eng.person)

    return render(
        request,
        "hr/engagement_form.html",
        {
            "form": form,
            "person": old_eng.person,
            "previous_engagement": old_eng,
            "is_renew": True,
            "is_edit": False,
        },
    )


@login_required
def document_create_person(request, person_pk):
    """Upload or record a document for a Person (HRMO Staff only)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to upload or manage HR documents.")

    person = get_object_or_404(Person, pk=person_pk)
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES, person=person)
        if form.is_valid():
            with transaction.atomic():
                doc = form.save(commit=False)
                doc.uploaded_by = request.user
                doc.version = 1
                doc.save()
            messages.success(request, f"Document '{doc.filename_for_display}' recorded for {person.full_name}.")
            return redirect("person_detail", pk=person.pk)
    else:
        form = DocumentForm(person=person)

    return render(
        request,
        "hr/document_form.html",
        {
            "form": form,
            "person": person,
            "target_name": person.full_name,
            "is_create": True,
        },
    )


@login_required
def document_create_engagement(request, engagement_pk):
    """Upload or record a document for an Engagement (HRMO Staff only)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to upload or manage HR documents.")

    engagement = get_object_or_404(Engagement, pk=engagement_pk)
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES, engagement=engagement)
        if form.is_valid():
            with transaction.atomic():
                doc = form.save(commit=False)
                doc.uploaded_by = request.user
                doc.version = 1
                doc.save()
            messages.success(request, f"Document '{doc.filename_for_display}' recorded for {engagement.position_title}.")
            return redirect("person_detail", pk=engagement.person_id)
    else:
        form = DocumentForm(engagement=engagement)

    return render(
        request,
        "hr/document_form.html",
        {
            "form": form,
            "engagement": engagement,
            "person": engagement.person,
            "target_name": f"{engagement.person.full_name} – {engagement.position_title}",
            "is_create": True,
        },
    )


@login_required
def document_edit(request, pk):
    """Edit metadata of an existing document (HRMO Staff only)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to edit HR documents.")

    doc = get_object_or_404(Document, pk=pk)
    target_person = doc.person or (doc.engagement.person if doc.engagement else None)

    if request.method == "POST":
        form = DocumentEditForm(request.POST, instance=doc)
        if form.is_valid():
            with transaction.atomic():
                locked_doc = Document.objects.select_for_update().get(pk=doc.pk)
                submitted_version = form.cleaned_data.get("version")
                if locked_doc.version != submitted_version:
                    form.add_error(
                        None,
                        "Conflict: This document record was modified by another user while you were editing it. "
                        "Please refresh the page and review the latest details before submitting changes.",
                    )
                else:
                    saved_doc = form.save(commit=False)
                    saved_doc.version = locked_doc.version + 1
                    saved_doc.save()
                    messages.success(request, f"Document metadata updated for '{saved_doc.filename_for_display}'.")
                    if target_person:
                        return redirect("person_detail", pk=target_person.pk)
                    return redirect("person_list")
    else:
        form = DocumentEditForm(instance=doc)

    return render(
        request,
        "hr/document_edit.html",
        {
            "form": form,
            "document": doc,
            "person": target_person,
        },
    )


@login_required
def document_replace(request, pk):
    """Upload a replacement scan for an existing document without deleting old scan (HRMO Staff only)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to replace HR document scans.")

    old_doc = get_object_or_404(Document, pk=pk)
    target_person = old_doc.person or (old_doc.engagement.person if old_doc.engagement else None)

    # Only the current scan can be replaced; replacing a superseded one would fork the
    # replacement history into two "current" documents.
    superseded_msg = "This scan has already been superseded. Replace the current version instead."
    if old_doc.is_replaced:
        messages.error(request, superseded_msg)
        if target_person:
            return redirect("person_detail", pk=target_person.pk)
        return redirect("person_list")

    if request.method == "POST":
        form = DocumentReplaceForm(request.POST, request.FILES, document=old_doc)
        if form.is_valid():
            with transaction.atomic():
                locked_old = Document.objects.select_for_update().get(pk=old_doc.pk)
                submitted_version = form.cleaned_data.get("version")
                if locked_old.is_replaced:
                    form.add_error(None, superseded_msg)
                elif locked_old.version != submitted_version:
                    form.add_error(
                        None,
                        "Conflict: This document record was modified by another user. "
                        "Please refresh and review the latest details before uploading a replacement.",
                    )
                else:
                    new_file = form.cleaned_data["file"]
                    new_doc = Document(
                        person=locked_old.person,
                        engagement=locked_old.engagement,
                        doc_type=locked_old.doc_type,
                        status=locked_old.status,
                        file=new_file,
                        display_name=sanitize_filename(new_file.name),
                        original_on_file=form.cleaned_data.get("original_on_file", True),
                        physical_location=form.cleaned_data.get("physical_location") or locked_old.physical_location,
                        notes=form.cleaned_data.get("notes") or f"Replacement scan for doc #{locked_old.pk}",
                        uploaded_by=request.user,
                        version=1,
                    )
                    new_doc.save()

                    locked_old.is_replaced = True
                    locked_old.replaced_by = new_doc
                    locked_old.version = locked_old.version + 1
                    locked_old.save()

                    messages.success(
                        request,
                        f"Replacement scan uploaded successfully for '{new_doc.filename_for_display}'. "
                        "The previous scan has been preserved in audit history as superseded.",
                    )
                    if target_person:
                        return redirect("person_detail", pk=target_person.pk)
                    return redirect("person_list")
    else:
        form = DocumentReplaceForm(document=old_doc)

    return render(
        request,
        "hr/document_replace.html",
        {
            "form": form,
            "document": old_doc,
            "person": target_person,
        },
    )


@login_required
def document_download(request, pk):
    """Private file serving for uploaded documents, checking permissions and enforcing safe headers."""
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

    filename = doc.display_name or os.path.basename(doc.file.name)
    response = FileResponse(doc.file.open("rb"), as_attachment=True, filename=filename)
    response["X-Content-Type-Options"] = "nosniff"
    return response


# --- Projects (DECISIONS P1-P4) ------------------------------------------------


@login_required
def project_list(request):
    """LGU projects with active headcount. Visible to Project Maintainers and all-office roles."""
    if not can_view_projects(request.user):
        raise PermissionDenied("You do not have permission to view projects.")

    status = request.GET.get("status", "").strip()
    if status not in Project.Status.values:
        status = ""
    qs = Project.objects.select_related("office").annotate(
        active_headcount=Count(
            "engagements", filter=Q(engagements__status=Engagement.Status.ACTIVE), distinct=True
        )
    )
    if status:
        qs = qs.filter(status=status)

    return render(
        request,
        "hr/project_list.html",
        {
            "projects": qs,
            "statuses": Project.Status.choices,
            "selected_status": status,
            "can_manage": can_manage_projects(request.user),
        },
    )


@login_required
def project_detail(request, pk):
    """Project details and headcount; the staff roster only for all-office HR roles."""
    if not can_view_projects(request.user):
        raise PermissionDenied("You do not have permission to view projects.")

    project = get_object_or_404(Project.objects.select_related("office"), pk=pk)
    active = project.engagements.filter(status=Engagement.Status.ACTIVE)
    ending_soon_by = timezone.localdate() + timedelta(days=30)
    show_roster = can_view_project_roster(request.user)

    return render(
        request,
        "hr/project_detail.html",
        {
            "project": project,
            "active_count": active.count(),
            "ending_soon_count": active.filter(end_date__lte=ending_soon_by).count(),
            "ended_count": project.engagements.exclude(status=Engagement.Status.ACTIVE).count(),
            "roster": active.select_related("person").order_by("end_date", "person__last_name") if show_roster else None,
            "show_roster": show_roster,
            "can_manage": can_manage_projects(request.user),
            "closed_with_active": project.status not in Project.HIRING_STATUSES and active.exists(),
        },
    )


@login_required
def project_create(request):
    """Record a new LGU project (Project Maintainer only)."""
    if not can_manage_projects(request.user):
        raise PermissionDenied("You do not have permission to create projects.")

    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                project = form.save(commit=False)
                project.version = 1
                project.save()
            messages.success(request, f"Project '{project.name}' recorded.")
            return redirect("project_detail", pk=project.pk)
    else:
        form = ProjectForm()

    return render(request, "hr/project_form.html", {"form": form, "is_edit": False})


@login_required
def project_edit(request, pk):
    """Edit a project's dates, status or funding reference, with lost-update protection."""
    if not can_manage_projects(request.user):
        raise PermissionDenied("You do not have permission to edit projects.")

    project = get_object_or_404(Project, pk=pk)

    if request.method == "POST":
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            with transaction.atomic():
                locked = Project.objects.select_for_update().get(pk=pk)
                if locked.version != form.cleaned_data.get("version"):
                    form.add_error(
                        None,
                        "This project was modified by another user while you were editing it. "
                        "Please refresh the page and review the changes before saving.",
                    )
                else:
                    saved = form.save(commit=False)
                    saved.version = locked.version + 1
                    saved.save()
                    messages.success(request, f"Project '{saved.name}' updated.")
                    return redirect("project_detail", pk=saved.pk)
    else:
        form = ProjectForm(instance=project)

    return render(request, "hr/project_form.html", {"form": form, "project": project, "is_edit": True})


def _parse_date_param(value):
    """Returns a date for a YYYY-MM-DD query parameter, or None if missing or invalid."""
    try:
        return parse_date(value) if value else None
    except ValueError:  # well-formed but impossible, e.g. 2026-02-31
        return None


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
    # Malformed dates are ignored instead of failing with a 500.
    start = _parse_date_param(start_date)
    end = _parse_date_param(end_date)
    if start:
        qs = qs.filter(timestamp__date__gte=start)
    if end:
        qs = qs.filter(timestamp__date__lte=end)

    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Show the stored result of the last full scan; never verify the chain on page load.
    verification = AuditChainStatus.objects.filter(pk=1).first()
    entries_since = 0
    if verification:
        entries_since = AuditLog.objects.filter(id__gt=verification.last_record_id or 0).count()

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
            "verification": verification,
            "entries_since": entries_since,
            "default_anchor": getattr(settings, "AUDIT_ANCHOR", None) or "",
        },
    )


REVERIFY_MIN_INTERVAL = timedelta(minutes=1)


@login_required
@require_POST
def audit_reverify(request):
    """Auditor-triggered full hash-chain scan, at most once per minute, logged in the audit trail."""
    if not (request.user.is_superuser or is_auditor(request.user) or request.user.has_perm("hr.view_audit_log")):
        return HttpResponseForbidden("You do not have permission to verify the audit log.")

    anchor = request.POST.get("anchor", "").strip() or getattr(settings, "AUDIT_ANCHOR", None)

    with transaction.atomic():
        # Row lock serializes concurrent clicks so the rate limit cannot be raced.
        last = AuditChainStatus.objects.select_for_update().filter(pk=1).first()
        if last and timezone.now() - last.verified_at < REVERIFY_MIN_INTERVAL:
            messages.warning(
                request,
                "The audit chain can be re-verified at most once per minute. "
                f"It was last verified at {timezone.localtime(last.verified_at):%Y-%m-%d %H:%M:%S}.",
            )
            return redirect("audit_list")

        result = run_and_record_verification(
            anchor=anchor, source="web", username=request.user.get_username()
        )
        log_audit_event(
            action=AuditLog.Action.VERIFY_CHAIN,
            model_name="AuditLog",
            object_repr="Re-verify all (full hash-chain scan)",
            changes={
                "is_valid": result.is_valid,
                "record_count": result.count,
                "broken_id": result.broken_id,
                "anchor_verified": result.anchor_verified,
            },
            request=request,
        )

    if result.is_valid:
        messages.success(request, f"Audit chain verified: {result.count} record(s) intact.")
    else:
        messages.error(request, f"Audit chain verification failed: {result.error_msg}")
    return redirect("audit_list")


# Leading characters that make Excel / LibreOffice treat a cell as a formula.
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Prefixes formula-like text with ' so a spreadsheet shows it instead of running it."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(CSV_FORMULA_PREFIXES) else text


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
        "Model", "Object ID", "Object Description", "Changes", "IP Address",
        "Request ID", "Session Key Hash", "Prev Hash", "Row Hash",
    ])

    qs = AuditLog.objects.all().order_by("-timestamp")[:5000]
    for row in qs:
        writer.writerow([csv_safe(value) for value in (
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
            row.request_id or "",
            row.session_key_hash or "",
            row.prev_hash or "",
            row.row_hash or "",
        )])

    return response

