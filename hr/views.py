from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from .models import Document, Engagement, LeaveCredit, LeaveType, Office, Person


@login_required
def office_list(request):
    """Directory of municipal offices with staff counts."""
    query = request.GET.get("q", "").strip()
    branch = request.GET.get("branch", "").strip()
    office_type = request.GET.get("type", "").strip()

    qs = Office.objects.annotate(
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
    """Office details and staff roster."""
    office = get_object_or_404(Office.objects.select_related("parent"), pk=pk)
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
    """Searchable, filterable list of personnel."""
    query = request.GET.get("q", "").strip()
    office_id = request.GET.get("office", "").strip()
    eng_type = request.GET.get("type", "").strip()
    status = request.GET.get("status", "").strip()

    qs = Person.objects.prefetch_related(
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

    offices = Office.objects.all().order_by("name")

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
    person = get_object_or_404(
        Person.objects.prefetch_related("engagements__office", "engagements__project", "documents"),
        pk=pk,
    )

    # Pay rate fields visible only to HRMO and auditors (DECISIONS R6).
    # Prior to full role system in Step 4, staff/superuser check is the baseline.
    can_view_rates = request.user.is_staff or request.user.is_superuser

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

    return render(
        request,
        "hr/person_detail.html",
        {
            "person": person,
            "engagements": engagements,
            "documents": documents,
            "can_view_rates": can_view_rates,
            "leave_balances": leave_balances,
        },
    )
