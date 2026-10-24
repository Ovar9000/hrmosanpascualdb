import csv
import json
import os
from io import BytesIO
from calendar import Calendar
from urllib.parse import urlencode
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
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from . import meetings, orgchart, reminders, salary_actions
from .audit import log_audit_event, run_and_record_verification
from .forms import (
    CalendarEventForm,
    DocumentEditForm,
    DocumentForm,
    DocumentReplaceForm,
    EndEngagementForm,
    EngagementForm,
    MeetingSeriesForm,
    PersonForm,
    ProjectForm,
    ServiceInterruptionForm,
    sanitize_filename,
)
from .models import (
    AuditChainStatus,
    AuditLog,
    CalendarEvent,
    Document,
    Engagement,
    LeaveCredit,
    LeaveRequest,
    LeaveType,
    Meeting,
    MeetingSeries,
    Memo,
    Office,
    Person,
    Project,
    SalaryAction,
    UserProfile,
    Vacancy,
)
from .permissions import (
    can_manage_hr_records,
    can_manage_projects,
    can_manage_salary_actions,
    can_view_contacts,
    can_view_document_content,
    can_view_chart_types,
    can_view_document_list,
    can_view_hr_notes,
    can_view_ids,
    can_view_office_chart,
    can_view_office_tree,
    can_view_pay,
    can_view_personal_details,
    can_approve_leave,
    can_view_project_roster,
    can_view_projects,
    can_view_salary_actions,
    can_view_trackers,
    get_user_office,
    get_visible_offices,
    get_visible_people,
    has_all_office_scope,
    is_auditor,
    is_hrmo,
    is_office_head,
    is_office_staff,
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
            profile.password_expires_at = None
            profile.save()
        return response


@login_required
def home(request):
    """Landing page: Project Maintainers with no personnel access go to Projects; everyone else gets
    the My HR dashboard (DECISIONS L8)."""
    if can_manage_projects(request.user) and not get_visible_people(request.user).exists():
        return redirect("project_list")
    if is_sysadmin(request.user) and not request.user.is_superuser:
        return redirect("person_list")
    from .board_views import dashboard

    return dashboard(request)


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


# --- Org charts (DECISIONS O1-O5) -----------------------------------------------


def _chart_or_403(request, office):
    if is_sysadmin(request.user) and not request.user.is_superuser:
        raise PermissionDenied("System administrators have no access to HR records.")
    if not can_view_office_chart(request.user, office):
        raise PermissionDenied("You can only see the org chart of your own office.")


def _viewer_engagement(user, office):
    """The viewer's own active engagement in this office, if any (for "You" on the chart)."""
    person_id = getattr(getattr(user, "profile", None), "person_id", None)
    if not person_id:
        return None
    return (
        office.engagements.filter(person_id=person_id, status=Engagement.Status.ACTIVE)
        .select_related("reports_to__person")
        .order_by("-start_date")
        .first()
    )


def _staff_chart(request, office):
    you = _viewer_engagement(request.user, office)
    candidates = Engagement.objects.filter(office=office, status=Engagement.Status.ACTIVE).values("person_id")
    linked = set(get_visible_people(request.user).filter(pk__in=candidates).values_list("pk", flat=True))
    staff = orgchart.build_staff_chart(
        office,
        show_types=can_view_chart_types(request.user),
        linked_person_ids=linked,
        you=you if you and you.on_chart else None,
    )
    return staff, you


def _not_drawn_note(not_drawn):
    if not not_drawn:
        return ""
    parts = ", ".join(f"{n} {label}" for label, n in sorted(not_drawn.items()))
    return f"Not drawn: {parts}. "


def _office_tree_scope(user):
    """Offices whose head and headcount the viewer may see, and whose chart they may open."""
    ids = set(get_visible_offices(user).values_list("pk", flat=True))
    own = get_user_office(user)
    if own is not None:
        ids.add(own.pk)
    return ids


def _tree_view(request):
    """Which office tree to draw (DECISIONS O6): ?office=<pk> makes that office the head,
    ?all=1 draws everything, otherwise the municipality and its top offices.
    Returns (focus office or None, whole, query string for the export links)."""
    if request.GET.get("all"):
        return None, True, "?all=1"
    value = request.GET.get("office", "")
    focus = Office.objects.filter(pk=value).first() if value.isdigit() else None
    return focus, False, (f"?office={focus.pk}" if focus else "")


@login_required
def office_tree(request):
    """The municipal office tree, from Office.parent."""
    if not can_view_office_tree(request.user):
        raise PermissionDenied("You do not have access to the office tree.")
    scope = _office_tree_scope(request.user)
    focus, whole, query = _tree_view(request)
    chart = orgchart.build_office_tree(
        detail_office_ids=scope, linked_office_ids=scope, focus=focus and focus.pk, whole=whole
    )
    heading = focus.name if focus else "Municipal office tree"
    return render(
        request,
        "hr/org_chart.html",
        {
            "heading": heading,
            "focus": focus,
            "whole": whole,
            "trail": chart.trail,
            "legend": [(label, orgchart.STYLES[s], orgchart.FILLS.get(s, "#fff")) for label, s in chart.legend],
            "svg": orgchart.render_svg(chart, f"Office tree: {heading}"),
            "export_urls": {f: reverse("office_tree_export", args=[f]) + query for f in orgchart.EXPORTS},
            "is_tree": True,
            "own_office": get_user_office(request.user),
            "focus_can_open": focus is not None and focus.pk in scope,
            "whole_url": orgchart.tree_url(whole=True),
        },
    )


@login_required
def office_chart(request, pk):
    """One office's staff chart, with the viewer's place in it."""
    office = get_object_or_404(Office.objects.select_related("parent"), pk=pk)
    _chart_or_403(request, office)
    staff, you = _staff_chart(request, office)

    supervisor = None
    if you and you.reports_to_id and you.reports_to.status == Engagement.Status.ACTIVE:
        supervisor = you.reports_to
    coworkers = []
    if you:
        coworkers = list(
            office.engagements.filter(status=Engagement.Status.ACTIVE)
            .exclude(person_id=you.person_id)
            .select_related("person")
            .order_by("person__last_name", "person__first_name")
        )
    return render(
        request,
        "hr/org_chart.html",
        {
            "heading": office.name,
            "office": office,
            "svg": orgchart.render_svg(staff.chart, f"Org chart of {office.name}"),
            "staff": staff,
            "not_drawn_note": _not_drawn_note(staff.not_drawn),
            "export_urls": {f: reverse("office_chart_export", args=[office.pk, f]) for f in orgchart.EXPORTS},
            "you": you,
            "supervisor": supervisor,
            "coworkers": coworkers,
            "can_edit": can_manage_hr_records(request.user),
            "can_view_tree": can_view_office_tree(request.user),
            "tree_url": orgchart.tree_url(office.parent_id),  # this office among its siblings
        },
    )


@login_required
def my_office(request):
    """Shortcut for employees: the chart of the office they work in."""
    office = get_user_office(request.user)
    if office is None:
        messages.info(request, "Your account is not linked to an office yet. Ask HRMO to link it.")
        return redirect("home")
    return redirect("office_chart", pk=office.pk)


def _export_response(request, fmt, chart, heading, note, filename, object_id):
    if fmt not in orgchart.EXPORTS:
        raise Http404("Unknown export format.")
    content_type, renderer = orgchart.EXPORTS[fmt]
    content = renderer(chart, heading, note)
    # Logged once the file exists, like the audit CSV export (DECISIONS U1).
    log_audit_event(
        action=AuditLog.Action.EXPORT,
        model_name="Office",
        object_id=str(object_id),
        object_repr=f"Org chart {fmt.upper()}: {heading}"[:255],
        request=request,
    )
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}.{fmt}"'
    return response


@login_required
def office_tree_export(request, fmt):
    if not can_view_office_tree(request.user):
        raise PermissionDenied("You do not have access to the office tree.")
    focus, whole, _query = _tree_view(request)
    chart = orgchart.build_office_tree(
        detail_office_ids=_office_tree_scope(request.user), focus=focus and focus.pk, whole=whole
    )
    heading = focus.name if focus else "Municipal Government of San Pascual"
    name = slugify(focus.acronym or focus.name) if focus else "san-pascual"
    return _export_response(
        request, fmt, chart, heading, orgchart.export_note("Organizational structure. "),
        f"office-tree-{name}-{timezone.localdate():%Y-%m-%d}", focus.pk if focus else "",
    )


@login_required
def office_chart_export(request, pk, fmt):
    office = get_object_or_404(Office, pk=pk)
    _chart_or_403(request, office)
    staff, _you = _staff_chart(request, office)
    name = slugify(office.acronym or office.name) or "office"
    return _export_response(
        request, fmt, staff.chart, f"{office.name} org chart",
        orgchart.export_note(_not_drawn_note(staff.not_drawn)),
        f"org-chart-{name}-{timezone.localdate():%Y-%m-%d}", office.pk,
    )


@login_required
def office_chart_edit(request, pk):
    """HRMO edits a whole office's reporting lines on one screen (DECISIONS O2).

    Only rows present in the POST are changed, each supervisor must come from this office's
    active chart appointments, and every changed row passes the S2 version check.
    """
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to edit reporting lines.")
    office = get_object_or_404(Office, pk=pk)
    active = sorted(
        office.engagements.filter(status=Engagement.Status.ACTIVE).select_related("person", "reports_to__person"),
        key=lambda e: (e.reports_to_id is not None, orgchart.sort_key(e)),  # heads first
    )
    by_pk = {e.pk: e for e in active}
    supervisors = [e for e in active if e.on_chart]

    proposed = {e.pk: e.reports_to_id for e in active}
    errors = {}
    if request.method == "POST":
        for eng in active:
            key = f"reports_to_{eng.pk}"
            if key not in request.POST:
                continue
            raw = request.POST[key].strip()
            if raw == "":
                proposed[eng.pk] = None
            elif raw.isdigit() and int(raw) in by_pk:
                proposed[eng.pk] = int(raw)
            else:
                errors[eng.pk] = "Choose someone from the list."
        for eng_pk, message in orgchart.reporting_line_errors(by_pk, proposed).items():
            errors.setdefault(eng_pk, message)

        if not errors:
            changed = [e for e in active if proposed[e.pk] != e.reports_to_id]
            conflict = False
            with transaction.atomic():
                locked = Engagement.objects.select_for_update().in_bulk([e.pk for e in changed])
                for eng in changed:
                    row = locked.get(eng.pk)
                    posted_version = request.POST.get(f"version_{eng.pk}", "")
                    if row is None or str(row.version) != posted_version or row.status != Engagement.Status.ACTIVE:
                        conflict = True
                if not conflict:
                    for eng in changed:
                        row = locked[eng.pk]
                        row.reports_to_id = proposed[eng.pk]
                        row.version += 1
                        row.save()
            if conflict:
                messages.error(
                    request,
                    "Someone changed these appointments while you were editing. Nothing was saved; "
                    "the page now shows the latest data.",
                )
                return redirect("office_chart_edit", pk=office.pk)
            if changed:
                messages.success(request, f"Saved {len(changed)} reporting line change(s) for {office.name}.")
            else:
                messages.info(request, "No changes to save.")
            return redirect("office_chart", pk=office.pk)

    def row(eng):
        return {
            "eng": eng,
            "selected": proposed.get(eng.pk),
            "error": errors.get(eng.pk, ""),
            "options": [s for s in supervisors if s.pk != eng.pk],
            "broken": bool(eng.reports_to_id and eng.reports_to_id not in by_pk),
        }

    return render(
        request,
        "hr/org_chart_edit.html",
        {
            "office": office,
            "chart_rows": [row(e) for e in active if e.on_chart],
            "other_rows": [row(e) for e in active if not e.on_chart],
            "has_errors": bool(errors),
            "other_has_errors": any(not e.on_chart and e.pk in errors for e in active),
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
            "salary": _person_salary_summary(request.user, person),
        },
    )



def _person_salary_summary(user, person):
    """Next step increment and loyalty dates plus open cases, for HRMO and Auditor (SA4)."""
    if not can_view_salary_actions(user):
        return None
    return {
        "next": salary_actions.next_milestones(person),
        "open_cases": person.salary_actions.filter(status__in=SalaryAction.OPEN_STATUSES).order_by("due_date"),
    }


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
                if locked_eng.status == Engagement.Status.ACTIVE:
                    orgchart.hand_over_direct_reports(saved_eng)

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
                orgchart.hand_over_direct_reports(locked_eng)

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
                old_eng.refresh_from_db()
                orgchart.hand_over_direct_reports(old_eng)

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
            "reports_to": old_eng.reports_to_id,
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


# --- Calendar (DECISIONS C1-C3) -------------------------------------------------

# --- Salary actions tracker (DECISIONS SA1-SA6) ---------------------------------

SALARY_TABS = [
    ("due", "Due", [SalaryAction.Status.DUE]),
    ("upcoming", "Upcoming", [SalaryAction.Status.UPCOMING]),
    ("review", "Under review", [SalaryAction.Status.REVIEW]),
    ("signature", "Awaiting signature", [SalaryAction.Status.SIGNATURE]),
    ("deferred", "Deferred", [SalaryAction.Status.DEFERRED]),
    ("closed", "Closed", SalaryAction.CLOSED_STATUSES),
]


def _require_salary_view(request):
    if is_sysadmin(request.user) and not request.user.is_superuser:
        raise PermissionDenied("System administrators have no access to HR records.")
    if not can_view_salary_actions(request.user):
        raise PermissionDenied("Salary increases and loyalty awards are visible to HRMO and Auditor only.")


@login_required
def salary_action_list(request):
    """The tracker: cases by stage, filtered by type and office."""
    _require_salary_view(request)
    last = salary_actions.run_if_stale(source="page")

    tabs = {key: statuses for key, _label, statuses in SALARY_TABS}
    tab = request.GET.get("tab", "due")
    if tab not in tabs:
        tab = "due"
    kind = request.GET.get("kind", "")
    if kind not in SalaryAction.Kind.values:
        kind = ""
    office = None
    office_id = request.GET.get("office", "")
    if office_id.isdigit():
        office = Office.objects.filter(pk=int(office_id)).first()

    base = SalaryAction.objects.select_related("person", "engagement__office")
    if kind:
        base = base.filter(kind=kind)
    if office:
        base = base.filter(engagement__office=office)
    counts = {key: base.filter(status__in=statuses).count() for key, statuses in tabs.items()}
    cases = base.filter(status__in=tabs[tab])
    cases = cases.order_by("-updated_at") if tab == "closed" else cases.order_by("due_date", "person__last_name")
    page = Paginator(cases, 50).get_page(request.GET.get("page"))

    params = [(k, v) for k, v in (("kind", kind), ("office", office.pk if office else "")) if v]
    return render(
        request,
        "hr/salary_action_list.html",
        {
            "tabs": [(key, label, counts[key]) for key, label, _statuses in SALARY_TABS],
            "tab": tab,
            "page": page,
            "kind": kind,
            "kinds": SalaryAction.Kind.choices,
            "office": office,
            "offices": Office.objects.order_by("name"),
            "filter_qs": "&" + urlencode(params) if params else "",
            "today": timezone.localdate(),
            "last_run": last,
            "can_manage": can_manage_salary_actions(request.user),
            "overdue": base.filter(status=SalaryAction.Status.DUE, due_date__lt=timezone.localdate()).count(),
        },
    )


@login_required
@require_POST
def salary_action_check_now(request):
    if not can_manage_salary_actions(request.user):
        raise PermissionDenied("Only HRMO can run the check.")
    created, updated = salary_actions.run_check(source="button")
    messages.success(request, f"Check finished: {created} new case(s), {updated} updated.")
    return redirect("salary_action_list")


def _parse_posted_date(request, name):
    try:
        return parse_date(request.POST.get(name, "").strip())
    except ValueError:  # well-formed but impossible, e.g. 2026-02-30
        return None


@login_required
def salary_action_detail(request, pk):
    """One case: the counted service, warnings, HRMO's checklist and the next step."""
    _require_salary_view(request)
    case = get_object_or_404(SalaryAction.objects.select_related("person", "engagement__office", "issued_by"), pk=pk)
    can_manage = can_manage_salary_actions(request.user)
    today = timezone.localdate()

    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Only HRMO can work on salary actions.")
        action = request.POST.get("action", "")
        try:
            with transaction.atomic():
                locked = SalaryAction.objects.select_for_update().get(pk=pk)
                if str(locked.version) != request.POST.get("version", ""):
                    raise salary_actions.WorkflowError(
                        "Someone changed this case while you had it open. Nothing was saved; check the latest version below."
                    )
                note = request.POST.get("note", "").strip()
                if action == "start_review":
                    salary_actions.start_review(locked)
                elif action == "checklist":
                    salary_actions.save_checklist(locked, set(request.POST.getlist("check")))
                elif action == "signature":
                    salary_actions.save_checklist(locked, set(request.POST.getlist("check")))
                    salary_actions.send_for_signature(locked)
                elif action == "defer":
                    salary_actions.defer(locked, _parse_posted_date(request, "deferred_until"), note, today)
                elif action == "not_eligible":
                    salary_actions.mark_not_eligible(locked, note)
                elif action == "issue":
                    salary_actions.issue(
                        locked,
                        request.POST.get("nosi_number", "").strip()[:50],
                        _parse_posted_date(request, "signed_on"),
                        _parse_posted_date(request, "effective_date"),
                        request.user,
                    )
                else:
                    raise salary_actions.WorkflowError("Unknown action.")
                locked.version += 1
                locked.save()
        except salary_actions.WorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{locked.get_kind_display()} for {case.person.full_name}: {locked.get_status_display()}.")
        return redirect("salary_action_detail", pk=pk)

    recomputed = salary_actions.next_milestones(case.person, today).get(case.kind)
    redated = None
    if recomputed and case.is_open and case.status not in SalaryAction.AUTO_STATUSES and recomputed["milestone"] == case.milestone:
        if recomputed["due_date"] != case.due_date:
            redated = recomputed["due_date"]
    return render(
        request,
        "hr/salary_action_detail.html",
        {
            "case": case,
            "can_manage": can_manage,
            "checklist": salary_actions.checklist_items(case),
            "engagements": case.person.engagements.select_related("office").order_by("-start_date"),
            "interruptions": case.person.service_interruptions.all(),
            "interruption_form": ServiceInterruptionForm(),
            "other_cases": case.person.salary_actions.exclude(pk=case.pk).order_by("-due_date"),
            "today": today,
            "redated": redated,
        },
    )


@login_required
@require_POST
def salary_interruption_create(request, pk):
    """HRMO records leave without pay or a suspension from a case page; the check re-dates open cases."""
    if not can_manage_salary_actions(request.user):
        raise PermissionDenied("Only HRMO can record service interruptions.")
    case = get_object_or_404(SalaryAction.objects.select_related("person"), pk=pk)
    form = ServiceInterruptionForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            item.person = case.person
            item.created_by = request.user
            item.save()
        salary_actions.run_check(person_ids=[case.person_id], source="button")
        messages.success(request, f"Recorded {item.get_kind_display().lower()} of {item.days} day(s). Open cases were re-dated.")
    else:
        errors = "; ".join(e for errs in form.errors.values() for e in errs)
        messages.error(request, f"Not recorded: {errors}")
    return redirect("salary_action_detail", pk=pk)


# --- Reminder lists: renewals, retirement, year-end bonus (DECISIONS RM1-RM5) ----------


def _require_tracker_view(request):
    if is_sysadmin(request.user) and not request.user.is_superuser:
        raise PermissionDenied("System administrators have no access to HR records.")
    if not can_view_trackers(request.user):
        raise PermissionDenied("These lists are for HRMO and Auditor only.")


def _xlsx_response(request, filename, sheet, headers, rows, audit_repr, model_name="Engagement"):
    """An Excel file of a reminder list. Text that looks like a formula is written as text (U1)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    ws = book.active
    ws.title = sheet[:31]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([csv_safe(v) if isinstance(v, str) else v for v in row])
    for column in ws.columns:
        width = max(len(str(c.value or "")) for c in column)
        ws.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 60)
    ws.freeze_panes = "A2"
    buf = BytesIO()
    book.save(buf)
    log_audit_event(action=AuditLog.Action.EXPORT, model_name=model_name, object_repr=audit_repr[:255], request=request)
    response = HttpResponse(
        buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _office_and_type_filters(request):
    office = None
    office_id = request.GET.get("office", "")
    if office_id.isdigit():
        office = Office.objects.filter(pk=int(office_id)).first()
    eng_type = request.GET.get("type", "")
    if eng_type not in Engagement.Type.values:
        eng_type = ""
    return office, eng_type


@login_required
def tracker_renewals(request):
    """Contracts that lapsed or end soon, so HRMO renews or ends them on time (RM1)."""
    _require_tracker_view(request)
    today = timezone.localdate()
    office, eng_type = _office_and_type_filters(request)
    rows = reminders.renewal_rows(today, office, eng_type)
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"contract-renewals-{today:%Y-%m-%d}.xlsx", "Contract renewals",
            ["Employee no.", "Name", "Office", "Type", "Position", "End date", "Days left", "Note"],
            [[r["eng"].person.employee_no, r["eng"].person.full_name, r["eng"].office.name,
              r["eng"].get_type_display(), r["eng"].position_title, r["eng"].end_date, r["days"],
              ("Already renewed: end this record" if r["renewed"] else "Lapsed: renew or end") if r["lapsed"] else ""]
             for r in rows],
            "Contract renewals list (Excel)",
        )
    params = [(k, v) for k, v in (("office", office.pk if office else ""), ("type", eng_type)) if v]
    return render(request, "hr/tracker_renewals.html", {
        "tracker": "renewals",
        "rows": rows,
        "lapsed": sum(1 for r in rows if r["lapsed"]),
        "window": settings.RENEWAL_LOOKAHEAD_DAYS,
        "offices": Office.objects.order_by("name"),
        "office": office,
        "types": Engagement.Type.choices,
        "eng_type": eng_type,
        "export_qs": urlencode(params + [("export", "xlsx")]),
        "can_manage": can_manage_hr_records(request.user),
    })


@login_required
def tracker_retirement(request):
    """Compulsory (65) and optional (60 with 15 years) retirement, from birthdates (RM2)."""
    _require_tracker_view(request)
    today = timezone.localdate()
    groups = reminders.retirement_rows(today)
    if request.GET.get("export") == "xlsx":
        labels = {"past": "Past 65, still active", "soon": "Turns 65 within a year", "optional": "Can retire optionally"}
        return _xlsx_response(
            request, f"retirement-{today:%Y-%m-%d}.xlsx", "Retirement",
            ["Group", "Employee no.", "Name", "Office", "Position", "Age", "Compulsory retirement date",
             "Unbroken service since", "Years of service"],
            [[labels[key], r["person"].employee_no, r["person"].full_name, r["eng"].office.name,
              r["eng"].position_title, r["age"], r["compulsory"], r["service_from"], r["service_years"]]
             for key in ("past", "soon", "optional") for r in groups[key]],
            "Retirement list (Excel)",
        )
    return render(request, "hr/tracker_retirement.html", {"tracker": "retirement", "groups": groups, "today": today})


@login_required
def tracker_year_end_bonus(request):
    """Who qualifies for the year-end bonus and cash gift as of October 31 (RM3). No amounts."""
    _require_tracker_view(request)
    today = timezone.localdate()
    years = list(range(today.year, today.year - 4, -1))
    year = request.GET.get("year", "")
    year = int(year) if year.isdigit() and int(year) in years else today.year
    office, eng_type = _office_and_type_filters(request)
    rows = reminders.yeb_rows(year, today)
    if office:
        rows = [r for r in rows if r["eng"].office_id == office.pk]
    if eng_type:
        rows = [r for r in rows if r["eng"].type == eng_type]
    if request.GET.get("export") == "xlsx":
        return _xlsx_response(
            request, f"year-end-bonus-{year}.xlsx", f"Year-end bonus {year}",
            ["Employee no.", "Name", "Office", "Type", "Position", "Days of service Jan 1 - Oct 31",
             "In service Oct 31", "Result"],
            [[r["person"].employee_no, r["person"].full_name, r["eng"].office.name, r["eng"].get_type_display(),
              r["eng"].position_title, r["days"], "Yes" if r["in_service"] else "No", r["label"]] for r in rows],
            f"Year-end bonus eligibility {year} (Excel)",
        )
    counts = {key: sum(1 for r in rows if r["result"] == key) for key in reminders.YEB_LABELS}
    params = [(k, v) for k, v in (("year", year), ("office", office.pk if office else ""), ("type", eng_type)) if v]
    return render(request, "hr/tracker_year_end_bonus.html", {
        "tracker": "yeb",
        "rows": rows,
        "year": year,
        "years": years,
        "projection": today < date(year, 10, 31),
        "counts": counts,
        "labels": reminders.YEB_LABELS,
        "offices": Office.objects.order_by("name"),
        "office": office,
        "types": [(v, l) for v, l in Engagement.Type.choices if v in reminders.YEB_TYPES],
        "eng_type": eng_type,
        "export_qs": urlencode(params + [("export", "xlsx")]),
        "min_days": reminders.YEB_MIN_DAYS,
    })


# --- Required meetings (DECISIONS MT1-MT5) -----------------------------------------


@login_required
def tracker_meetings(request):
    """Every required meeting with this period's status and the last few periods."""
    _require_tracker_view(request)
    today = timezone.localdate()
    meetings.ensure_meetings(today)
    rows = []
    for series in MeetingSeries.objects.select_related("office").prefetch_related("meetings"):
        periods = [m for m in series.meetings.all() if m.period_start <= today]  # newest first (Meta ordering)
        current = periods[0] if periods else None
        rows.append({
            "series": series,
            "current": current,
            "current_status": current.status_on(today) if current else None,
            "history": [(m, m.status_on(today)) for m in periods[1:5]],
            "missed": sum(1 for m in periods if m.status_on(today) == Meeting.Status.MISSED),
        })
    rows.sort(key=lambda r: (not r["series"].is_active, r["series"].name))
    return render(request, "hr/tracker_meetings.html", {
        "tracker": "meetings",
        "rows": rows,
        "today": today,
        "can_manage": can_manage_hr_records(request.user),
        "attention_days": meetings.ATTENTION_DAYS,
    })


@login_required
def meeting_series_form(request, pk=None):
    """HRMO adds or edits a required meeting (who attends, how often, legal basis)."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("Only HRMO can set up required meetings.")
    series = get_object_or_404(MeetingSeries, pk=pk) if pk else None
    if request.method == "POST":
        form = MeetingSeriesForm(request.POST, instance=series)
        if form.is_valid():
            with transaction.atomic():
                if series:
                    locked = MeetingSeries.objects.select_for_update().get(pk=series.pk)
                    if locked.version != form.cleaned_data.get("version"):
                        form.add_error(None, "Someone changed this meeting while you were editing. Reload and try again.")
                        return render(request, "hr/meeting_series_form.html", {"form": form, "series": series})
                saved = form.save(commit=False)
                if series:
                    saved.version = locked.version + 1
                else:
                    saved.created_by = request.user
                saved.save()
                form.save_m2m()
            meetings.ensure_meetings(timezone.localdate(), MeetingSeries.objects.filter(pk=saved.pk, is_active=True))
            messages.success(request, f"Saved {saved.name}.")
            return redirect("tracker_meetings")
    else:
        initial = {} if series else {"starts_on": timezone.localdate()}
        form = MeetingSeriesForm(instance=series, initial=initial)
    return render(request, "hr/meeting_series_form.html", {"form": form, "series": series})


@login_required
def meeting_detail(request, pk):
    """One period's meeting: schedule it, record it as held with attendance, or give a reason."""
    _require_tracker_view(request)
    meeting = get_object_or_404(Meeting.objects.select_related("series", "series__office"), pk=pk)
    today = timezone.localdate()
    expected = meetings.expected_people(meeting.series)
    use_checklist = meeting.series.audience != MeetingSeries.Audience.EVERYONE

    if request.method == "POST":
        if not can_manage_hr_records(request.user):
            raise PermissionDenied("Only HRMO can update required meetings.")
        action = request.POST.get("action", "")
        error = None
        with transaction.atomic():
            locked = Meeting.objects.select_for_update().get(pk=pk)
            if str(locked.version) != request.POST.get("version", ""):
                error = "Someone changed this meeting while you had it open. Nothing was saved."
            elif action == "schedule":
                day = _parse_posted_date(request, "scheduled_on")
                if day is None:
                    error = "Choose the meeting date."
                else:
                    locked.scheduled_on = day
                    locked.scheduled_time = _parse_posted_time(request, "scheduled_time")
                    locked.venue = request.POST.get("venue", "").strip()[:200]
            elif action == "held":
                day = _parse_posted_date(request, "held_on")
                if day is None or day > today:
                    error = "Enter the date it was held (today or earlier)."
                else:
                    locked.held_on = day
                    locked.not_held_reason = ""
                    locked.minutes_location = request.POST.get("minutes_location", "").strip()[:255]
                    locked.minutes_summary = request.POST.get("minutes_summary", "").strip()
                    if use_checklist:
                        allowed = set(expected.values_list("pk", flat=True))
                        picked = [int(v) for v in request.POST.getlist("attended") if v.isdigit() and int(v) in allowed]
                        locked.attendance_count = len(picked)
                    else:
                        count = request.POST.get("attendance_count", "").strip()
                        locked.attendance_count = int(count) if count.isdigit() else None
            elif action == "not_held":
                reason = request.POST.get("reason", "").strip()
                if not reason:
                    error = "Give the reason it was not held."
                else:
                    locked.not_held_reason = reason
                    locked.held_on = None
            elif action == "reopen":
                locked.held_on = None
                locked.not_held_reason = ""
            else:
                error = "Unknown action."
            if error is None:
                locked.version += 1
                locked.save()
                if action == "held" and use_checklist:
                    locked.attendees.set(picked)
                elif action in ("reopen", "not_held"):
                    locked.attendees.clear()
        if error:
            messages.error(request, error)
        else:
            messages.success(request, f"{locked}: {locked.status_on(today).label}.")
        return redirect("meeting_detail", pk=pk)

    attended = set(meeting.attendees.values_list("pk", flat=True))
    return render(request, "hr/meeting_detail.html", {
        "tracker": "meetings",
        "meeting": meeting,
        "status": meeting.status_on(today),
        "expected": expected,
        "expected_count": expected.count(),
        "use_checklist": use_checklist,
        "attended": attended,
        "today": today,
        "can_manage": can_manage_hr_records(request.user),
    })


def _parse_posted_time(request, name):
    from django.utils.dateparse import parse_time

    try:
        return parse_time(request.POST.get(name, "").strip())
    except ValueError:
        return None


# Every item kind, in the order a day lists them: (text prefix, CSS class, plural for a
# collapsed group). The prefix makes the kind readable without colour (C3).
CALENDAR_KINDS = {
    CalendarEvent.Kind.HOLIDAY: ("Holiday", "cal-holiday", "holidays"),
    CalendarEvent.Kind.DEADLINE: ("Due", "cal-deadline", "deadlines"),
    CalendarEvent.Kind.INTERVIEW: ("Interview", "cal-interview", "interviews"),
    CalendarEvent.Kind.MEETING: ("Meeting", "cal-meeting", "meetings"),
    CalendarEvent.Kind.OTHER: ("Event", "cal-other", "events"),
    "leave": ("Leave", "cal-leave", "people on leave"),
    "hire": ("Start", "cal-hire", "contract starts"),
    "contract_end": ("End", "cal-end", "contract ends"),
    "project": ("Project", "cal-project", "project dates"),
    "salary": ("Salary increase", "cal-salary", "salary increases"),
    "loyalty": ("Loyalty", "cal-loyalty", "loyalty awards"),
    "retirement": ("Retires", "cal-retire", "retirements"),
    "required_meeting": ("Required", "cal-required", "required meetings"),
    "vacancy": ("Apply by", "cal-deadline", "application deadlines"),
    "memo": ("Comply", "cal-deadline", "memo deadlines"),
}
CALENDAR_KIND_ORDER = list(CALENDAR_KINDS)

# Filter groups shown as checkboxes; "events" covers every CalendarEvent kind.
CALENDAR_FILTERS = [
    ("events", "HR events"),
    ("leave", "Leave"),
    ("hire", "Contract starts"),
    ("contract_end", "Contract ends"),
    ("project", "Project dates"),
    ("salary", "Salary increases and loyalty"),
    ("retirement", "Retirements (age 65)"),
    ("required_meeting", "Required meetings"),
    ("bulletin", "Vacancy and memo deadlines"),
]
CALENDAR_LEGEND = [
    (label, CALENDAR_KINDS[value][0], CALENDAR_KINDS[value][1]) for value, label in CalendarEvent.Kind.choices
] + [
    ("Leave", "Leave", "cal-leave"),
    ("Contract start", "Start", "cal-hire"),
    ("Contract end", "End", "cal-end"),
    ("Project start / end", "Project", "cal-project"),
    ("Salary increase due (HRMO, Auditor)", "Salary increase", "cal-salary"),
    ("Loyalty award due (HRMO, Auditor)", "Loyalty", "cal-loyalty"),
    ("Compulsory retirement at 65 (HRMO, Auditor)", "Retires", "cal-retire"),
    ("Required meeting (date, or the last day to hold it)", "Required", "cal-required"),
    ("Last day to apply for a vacancy", "Apply by", "cal-deadline"),
    ("Deadline set by a memo addressed to you", "Comply", "cal-deadline"),
]

# A day shows at most this many entries; a kind with more items than this collapses into
# one "N contract ends" entry that opens the day page (C3).
CALENDAR_CELL_LIMIT = 3


def _short_name(person):
    return f"{person.last_name}, {person.first_name[:1]}."


def _calendar_filters(request):
    """Validated filter choices from the query string: (kinds shown, office or None, querystring)."""
    groups = {value for value, _ in CALENDAR_FILTERS}
    if request.GET.get("filtered"):
        shown = {value for value in request.GET.getlist("show") if value in groups}
    else:
        shown = set(groups)
    office = None
    office_id = request.GET.get("office", "")
    if office_id.isdigit():
        # Only an office this role can already see; anything else is ignored (R9).
        office = get_visible_offices(request.user).filter(pk=int(office_id)).first()

    params = [("filtered", "1")] + [("show", value) for value, _ in CALENDAR_FILTERS if value in shown]
    if office:
        params.append(("office", office.pk))
    is_default = shown == groups and not office
    return shown, office, "" if is_default else "&" + urlencode(params)


def _calendar_items(user, first, last, shown, office=None):
    """Returns {day: [item, ...]} of everything this role may see between first and last (C2).

    Each item: kind, prefix, css, short (for a grid cell), full (complete text), url, note, office.
    """
    days = {}
    can_manage = can_manage_hr_records(user)

    def add(day, kind, short, full, url=None, note="", office_name=""):
        if first <= day <= last:
            prefix, css, _ = CALENDAR_KINDS[kind]
            days.setdefault(day, []).append({
                "kind": kind, "prefix": prefix, "css": css, "short": short, "full": full,
                "url": url, "note": note, "office": office_name,
            })

    def span(start, end):
        day = max(start, first)
        while day <= min(end, last):
            yield day
            day += timedelta(days=1)

    # Events: municipality-wide, plus those for an office the user oversees or works in.
    if "events" in shown:
        events = CalendarEvent.objects.filter(start_date__lte=last).filter(
            Q(end_date__gte=first) | Q(end_date__isnull=True, start_date__gte=first)
        ).select_related("office")
        if not has_all_office_scope(user):
            office_ids = list(get_visible_offices(user).values_list("pk", flat=True))
            own_office = get_user_office(user)
            if own_office:
                office_ids.append(own_office.pk)
            events = events.filter(Q(office__isnull=True) | Q(office__in=office_ids))
        if office:
            events = events.filter(Q(office__isnull=True) | Q(office=office))
        for event in events:
            # The prefix already names the kind: "Interview: Clerk applicants" shows as "Interview Clerk applicants".
            title = event.title
            kind_word = CALENDAR_KINDS[event.kind][0].lower() + ":"
            if title.lower().startswith(kind_word):
                title = title[len(kind_word):].strip() or event.title
            short = f"{event.time:%H:%M} {title}" if event.time else title
            full = event.title + (f" at {event.time:%H:%M}" if event.time else "")
            url = reverse("calendar_event_edit", args=[event.pk]) if can_manage else None
            office_name = event.office.name if event.office else "All offices"
            for day in span(event.start_date, event.end_date or event.start_date):
                add(day, event.kind, short, full, url, event.notes, office_name)

    people = get_visible_people(user)

    # Leave: approvers, HRMO and Auditor see everyone they can see; others only their own (R4).
    if "leave" in shown:
        leave_people = people
        if not (can_approve_leave(user) or can_view_personal_details(user)):
            leave_people = people.filter(user_profile__user=user)
        if office:
            leave_people = leave_people.filter(
                engagements__office=office, engagements__status=Engagement.Status.ACTIVE
            )
        leaves = LeaveRequest.objects.filter(
            person__in=leave_people,
            status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED],
            start_date__lte=last,
            end_date__gte=first,
        ).select_related("person")
        for leave in leaves:
            pending = " (pending)" if leave.status == LeaveRequest.Status.PENDING else ""
            full = f"{leave.person.full_name} · {leave.get_leave_type_display()}{pending}"
            url = reverse("leave_detail", args=[leave.pk])
            for day in span(leave.start_date, leave.end_date):
                add(day, "leave", _short_name(leave.person) + pending, full, url)

    # Contract starts and ends. Office roles see only their own office's engagements (R5).
    if "hire" in shown or "contract_end" in shown:
        engagements = Engagement.objects.filter(person__in=people).select_related("person", "office")
        if not has_all_office_scope(user) and (is_office_head(user) or is_office_staff(user)):
            engagements = engagements.filter(office__in=get_visible_offices(user))
        if office:
            engagements = engagements.filter(office=office)
        for eng in engagements.filter(Q(start_date__range=(first, last)) | Q(end_date__range=(first, last))):
            who = f"{eng.person.full_name}, {eng.position_title}"
            url = reverse("person_detail", args=[eng.person_id])
            if "hire" in shown:
                add(eng.start_date, "hire", _short_name(eng.person), f"Starts: {who}", url, office_name=eng.office.name)
            if eng.end_date and "contract_end" in shown:
                add(eng.end_date, "contract_end", _short_name(eng.person), f"Contract ends: {who}", url,
                    office_name=eng.office.name)

    if "project" in shown and can_view_projects(user):
        projects = Project.objects.filter(Q(start_date__range=(first, last)) | Q(end_date__range=(first, last)))
        if office:
            projects = projects.filter(office=office)
        for project in projects.select_related("office"):
            url = reverse("project_detail", args=[project.pk])
            add(project.start_date, "project", f"{project.name} (start)", f"Project starts: {project.name}", url,
                office_name=project.office.name)
            if project.end_date:
                add(project.end_date, "project", f"{project.name} (end)", f"Project ends: {project.name}", url,
                    office_name=project.office.name)

    # Open step increment and loyalty cases on their due dates: pay data, so HRMO and Auditor only (SA4).
    if "salary" in shown and can_view_salary_actions(user):
        salary_actions.run_if_stale(source="calendar")
        cases = SalaryAction.objects.filter(
            status__in=SalaryAction.OPEN_STATUSES, due_date__range=(first, last)
        ).select_related("person", "engagement__office")
        if office:
            cases = cases.filter(engagement__office=office)
        for case in cases:
            if case.kind == SalaryAction.Kind.STEP_INCREMENT:
                kind, what = "salary", f"step {case.from_step} → {case.to_step}"
            else:
                kind, what = "loyalty", f"{case.years} years"
            add(case.due_date, kind, f"{_short_name(case.person)} {what}",
                f"{case.get_kind_display()} {case.get_status_display().lower()}: {case.person.full_name}, {what}",
                reverse("salary_action_detail", args=[case.pk]), office_name=case.engagement.office.name)

    # Required meetings: everyone sees meetings for everyone; office meetings show to that office and
    # all-office roles; chosen-people meetings to the members and all-office roles (MT4).
    if "required_meeting" in shown:
        meetings.ensure_meetings(timezone.localdate())
        items = Meeting.objects.filter(series__is_active=True, period_start__lte=last, period_end__gte=first).filter(
            Q(scheduled_on__range=(first, last)) | Q(held_on__range=(first, last)) | Q(period_end__range=(first, last))
        ).select_related("series", "series__office")
        if not has_all_office_scope(user):
            office_ids = list(get_visible_offices(user).values_list("pk", flat=True))
            own_office = get_user_office(user)
            if own_office:
                office_ids.append(own_office.pk)
            person_id = getattr(getattr(user, "profile", None), "person_id", None)
            items = items.filter(
                Q(series__audience=MeetingSeries.Audience.EVERYONE)
                | Q(series__audience=MeetingSeries.Audience.OFFICE, series__office__in=office_ids)
                | Q(series__audience=MeetingSeries.Audience.CHOSEN, series__members=person_id or 0)
            ).distinct()
        if office:
            items = items.filter(Q(series__office__isnull=True) | Q(series__office=office))
        url_ok = can_view_trackers(user)
        for item in items:
            url = reverse("meeting_detail", args=[item.pk]) if url_ok else None
            label = f"{item.series.name} ({item.period_label})"
            if item.held_on:
                add(item.held_on, "required_meeting", item.series.name, f"Held: {label}", url)
            elif item.scheduled_on and not item.not_held_reason:
                when = f" at {item.scheduled_time:%H:%M}" if item.scheduled_time else ""
                where = f", {item.venue}" if item.venue else ""
                add(item.scheduled_on, "required_meeting", item.series.name, f"{label}{when}{where}", url)
            elif not item.not_held_reason:
                add(item.period_end, "required_meeting", f"Hold by today: {item.series.name}",
                    f"Last day to hold {label} (not scheduled yet)", url)

    # Vacancy deadlines are public notices; memo deadlines show to the memo's addressees and
    # all-office roles (V2, M4).
    if "bulletin" in shown:
        from . import board

        vacancies = Vacancy.objects.filter(status=Vacancy.Status.OPEN, deadline__range=(first, last))
        if office:
            vacancies = vacancies.filter(office=office)
        for vac in vacancies.select_related("office"):
            add(vac.deadline, "vacancy", vac.position_title, f"Last day to apply: {vac.position_title} ({vac.office.name})",
                reverse("vacancy_detail", args=[vac.pk]), office_name=vac.office.name)
        memos = Memo.objects.filter(is_published=True, comply_by__range=(first, last))
        if not has_all_office_scope(user):
            person = getattr(getattr(user, "profile", None), "person", None)
            memos = board.memos_for_person(person).filter(comply_by__range=(first, last))
        for memo in memos:
            add(memo.comply_by, "memo", memo.subject, f"Comply by today: {memo.number}, {memo.subject}",
                reverse("memo_detail", args=[memo.pk]))

    # 65th birthdays of active GSIS members: birthdates are personal details, so HRMO and Auditor (RM2).
    if "retirement" in shown and can_view_trackers(user):
        age = reminders.COMPULSORY_AGE
        retiring = Engagement.objects.filter(
            status=Engagement.Status.ACTIVE, type__in=reminders.GSIS_TYPES,
            person__birthdate__range=(salary_actions.add_years(first, -age), salary_actions.add_years(last, -age)),
        ).select_related("person", "office")
        if office:
            retiring = retiring.filter(office=office)
        seen = set()
        for eng in retiring:
            if eng.person_id in seen:
                continue
            seen.add(eng.person_id)
            add(salary_actions.add_years(eng.person.birthdate, age), "retirement", _short_name(eng.person),
                f"Turns {age} (compulsory retirement): {eng.person.full_name}, {eng.position_title}",
                reverse("tracker_retirement"), office_name=eng.office.name)

    for items in days.values():
        items.sort(key=lambda item: (CALENDAR_KIND_ORDER.index(item["kind"]), item["full"]))
    return days


def _compact_day(day, items, filter_qs):
    """Collapses any kind with more than CALENDAR_CELL_LIMIT items into one summary entry (C3)."""
    day_url = reverse("calendar_day", args=[day.isoformat()])
    compact, by_kind = [], {}
    for item in items:
        by_kind.setdefault(item["kind"], []).append(item)
    for kind, group in by_kind.items():
        if len(group) > CALENDAR_CELL_LIMIT:
            prefix, css, plural = CALENDAR_KINDS[kind]
            compact.append({
                "kind": kind, "prefix": prefix, "css": css, "summary": True,
                "short": f"{len(group)} {plural}", "full": f"{len(group)} {plural}: open the day for the full list",
                "url": f"{day_url}?{filter_qs.lstrip('&')}#{kind}" if filter_qs else f"{day_url}#{kind}",
            })
        else:
            compact.extend(group)
    return compact


def _calendar_redirect(day):
    return redirect(f"{reverse('calendar')}?month={day:%Y-%m}")


@login_required
def calendar_month(request):
    """Month grid (wide screens) and agenda list (phones) of role-scoped items (C1-C3)."""
    user = request.user
    if is_sysadmin(user) and not user.is_superuser:
        return redirect("admin:index")

    today = timezone.localdate()
    month = _parse_date_param(request.GET.get("month", "") + "-01")
    if not month or not 1900 <= month.year <= 2200:  # bad input falls back, never a 500
        month = today.replace(day=1)
    shown, office, filter_qs = _calendar_filters(request)
    weeks = Calendar(firstweekday=6).monthdatescalendar(month.year, month.month)  # Sunday first
    items = _calendar_items(user, weeks[0][0], weeks[-1][-1], shown, office)

    grid, agenda = [], []
    for week in weeks:
        row = []
        for day in week:
            compact = _compact_day(day, items.get(day, []), filter_qs)
            row.append({
                "date": day,
                "items": compact[:CALENDAR_CELL_LIMIT],
                "more": len(compact) - CALENDAR_CELL_LIMIT,
                "url": reverse("calendar_day", args=[day.isoformat()]),
            })
            if day.month == month.month and compact:
                agenda.append({"date": day, "items": compact})
        grid.append(row)

    visible_offices = get_visible_offices(user)
    return render(
        request,
        "hr/calendar.html",
        {
            "month": month,
            "today": today,
            "weeks": grid,
            "agenda": agenda,
            "prev_month": (month - timedelta(days=1)).replace(day=1),
            "next_month": (month + timedelta(days=31)).replace(day=1),
            "legend": CALENDAR_LEGEND,
            "filters": [(value, label, value in shown) for value, label in CALENDAR_FILTERS],
            "offices": visible_offices if visible_offices.count() > 1 else None,
            "selected_office": office,
            "filter_qs": filter_qs,
            "can_manage": can_manage_hr_records(user),
        },
    )


@login_required
def calendar_day(request, day):
    """Everything on one day, in full, grouped by kind (C3). Same scoping and filters as the month."""
    user = request.user
    if is_sysadmin(user) and not user.is_superuser:
        return redirect("admin:index")
    date_value = _parse_date_param(day)
    if not date_value or not 1900 <= date_value.year <= 2200:
        raise Http404("Not a valid date.")

    shown, office, filter_qs = _calendar_filters(request)
    items = _calendar_items(user, date_value, date_value, shown, office).get(date_value, [])
    groups = []
    for item in items:
        if not groups or groups[-1]["kind"] != item["kind"]:
            prefix, css, plural = CALENDAR_KINDS[item["kind"]]
            groups.append({"kind": item["kind"], "prefix": prefix, "css": css, "plural": plural, "items": []})
        groups[-1]["items"].append(item)
    for group in groups:
        group["items"].sort(key=lambda item: (item["office"], item["full"]))

    return render(
        request,
        "hr/calendar_day.html",
        {
            "day": date_value,
            "groups": groups,
            "count": len(items),
            "month_url": f"{reverse('calendar')}?month={date_value:%Y-%m}{filter_qs}",
            "prev_url": reverse("calendar_day", args=[(date_value - timedelta(days=1)).isoformat()]) + (
                f"?{filter_qs.lstrip('&')}" if filter_qs else ""),
            "next_url": reverse("calendar_day", args=[(date_value + timedelta(days=1)).isoformat()]) + (
                f"?{filter_qs.lstrip('&')}" if filter_qs else ""),
            "can_manage": can_manage_hr_records(user),
        },
    )


@login_required
def calendar_event_form(request, pk=None):
    """Add, edit or delete a calendar event (HRMO only, C1). Deletions are audit-logged."""
    if not can_manage_hr_records(request.user):
        raise PermissionDenied("You do not have permission to manage calendar events.")

    event = get_object_or_404(CalendarEvent, pk=pk) if pk else None
    if request.method == "POST" and event and "delete" in request.POST:
        with transaction.atomic():
            event.delete()
        messages.success(request, f"Removed '{event.title}' from the calendar.")
        return _calendar_redirect(event.start_date)

    form = CalendarEventForm(
        request.POST or None,
        instance=event,
        initial=None if event else {"start_date": _parse_date_param(request.GET.get("date", ""))},
    )
    if request.method == "POST" and form.is_valid():
        # No version check (S2) on events yet; add one if two HRMO staff start editing the same event.
        with transaction.atomic():
            saved = form.save(commit=False)
            if not event:
                saved.created_by = request.user
            saved.save()
        messages.success(request, f"Saved '{saved.title}' on the calendar.")
        return _calendar_redirect(saved.start_date)

    return render(request, "hr/calendar_event_form.html", {"form": form, "event": event})


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

