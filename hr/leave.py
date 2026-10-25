"""Leave filing, the CS Form No. 6 approval chain, and the leave ledger (DECISIONS L1-L9).

The form stays the legal record (F1): the system fills it in, routes it, keeps the ledger and
prints it for wet signatures. Every change is a normal .save(), so it is audit-logged (U6).
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    CalendarEvent, Engagement, LeaveApprovalStep, LeaveCredit, LeaveDetail, LeaveRequest, LeaveType, Person,
    UserProfile,
)
from .permissions import ROLE_OFFICE_HEAD, is_executive, is_hrmo, is_office_head

Step = LeaveApprovalStep

# Which ledger a leave type is charged to. Forced leave is vacation leave taken (L5).
CHARGED_TO = {LeaveType.VACATION: LeaveType.VACATION, LeaveType.MANDATORY: LeaveType.VACATION,
              LeaveType.SICK: LeaveType.SICK}
SPL_DAYS_PER_YEAR = Decimal("3")
FORCED_LEAVE_DAYS = Decimal("5")
FORCED_LEAVE_THRESHOLD = Decimal("10")
MONTHLY_EARNING = Decimal("1.25")

# What the instructions page of CS Form No. 6 asks for, shown while filing (L7).
GUIDANCE = {
    LeaveType.VACATION: "File at least 5 days before the leave. For travel abroad, say where.",
    LeaveType.MANDATORY: "Charged to your vacation leave. With 10 or more days of vacation leave, 5 days a year must "
                         "be taken or they are forfeited.",
    LeaveType.SICK: "File as soon as you return to work. More than 5 days, or sick leave filed ahead, needs a "
                    "medical certificate.",
    LeaveType.MATERNITY: "105 days (RA 11210). Attach proof of pregnancy, e.g. an ultrasound or doctor's certificate.",
    LeaveType.PATERNITY: "7 days for a married male employee, for each of the first 4 deliveries (RA 8187). "
                         "Attach proof of the child's delivery and the marriage contract.",
    LeaveType.SPECIAL_PRIVILEGE: "Up to 3 days a year, not cumulative. File at least a week ahead, except in "
                                 "emergencies.",
    LeaveType.SOLO_PARENT: "7 working days a year. Attach your Solo Parent ID (RA 8972, as amended).",
    LeaveType.STUDY: "Up to 6 months, with a contract between you and the LGU.",
    LeaveType.VAWC: "Up to 10 days (RA 9262). Attach a barangay protection order, or a police, prosecutor or court "
                    "certification.",
    LeaveType.REHABILITATION: "Up to 6 months, for injuries from the performance of duty. File within a week of "
                              "the accident, with the police report and medical certificate.",
    LeaveType.SPECIAL_WOMEN: "Up to 2 months after gynecological surgery (RA 9710). Attach the medical certificate.",
    LeaveType.CALAMITY: "Up to 5 days within 30 days of the calamity (CSC MC No. 2, s. 2012).",
    LeaveType.ADOPTION: "Attach the authenticated pre-adoptive placement authority from DSWD (RA 8552).",
    LeaveType.WELLNESS: "Wellness leave (CSC MC No. 1, s. 2026). HRMO will confirm how many days apply.",
    LeaveType.CTO: "Uses your compensatory overtime credits (COC), 8 hours a day or 4 for a half day. File it ahead "
                   "and agree the dates with your office head.",
    LeaveType.OTHER: "Say what leave this is. HRMO will confirm the rules that apply.",
}

# The legal basis printed under each type in section 6.A of the 2026 San Pascual form (L6).
LEGAL_BASIS = {
    LeaveType.VACATION: "Sec. 51, Rule XVI, Omnibus Rules Implementing E.O. No. 292",
    LeaveType.MANDATORY: "Sec. 25, Rule XVI, Omnibus Rules Implementing E.O. No. 292",
    LeaveType.SICK: "Sec. 43, Rule XVI, Omnibus Rules Implementing E.O. No. 292",
    LeaveType.MATERNITY: "R.A. No. 11210 / IRR issued by CSC, DOLE and SSS",
    LeaveType.PATERNITY: "R.A. No. 8187 / CSC MC No. 71, s. 1998, as amended",
    LeaveType.SPECIAL_PRIVILEGE: "Sec. 21, Rule XVI, Omnibus Rules Implementing E.O. No. 292",
    LeaveType.SOLO_PARENT: "RA No. 8972 / CSC MC No. 8, s. 2004",
    LeaveType.STUDY: "Sec. 68, Rule XVI, Omnibus Rules Implementing E.O. No. 292",
    LeaveType.VAWC: "RA No. 9262 / CSC MC No. 15, s. 2005",
    LeaveType.REHABILITATION: "Sec. 55, Rule XVI, Omnibus Rules Implementing E.O. No. 292",
    LeaveType.SPECIAL_WOMEN: "RA No. 9710 / CSC MC No. 25, s. 2010",
    LeaveType.CALAMITY: "CSC MC No. 2, s. 2012, as amended",
    LeaveType.ADOPTION: "R.A. No. 8552",
    LeaveType.WELLNESS: "CSC MC 01 s. 2026 Policy",
    LeaveType.CTO: "CSC-DBM Joint Circular No. 2, s. 2004",
}

# The section 6.B choices that fit each leave type.
TRAVEL = [LeaveDetail.WITHIN_PH, LeaveDetail.ABROAD]
PURPOSE = [LeaveDetail.MONETIZATION, LeaveDetail.TERMINAL]
DETAILS = {
    LeaveType.VACATION: TRAVEL + PURPOSE,
    LeaveType.MANDATORY: TRAVEL,
    LeaveType.SPECIAL_PRIVILEGE: TRAVEL,
    LeaveType.SICK: [LeaveDetail.IN_HOSPITAL, LeaveDetail.OUT_PATIENT] + PURPOSE,
    LeaveType.SPECIAL_WOMEN: [LeaveDetail.WOMEN_ILLNESS],
    LeaveType.STUDY: [LeaveDetail.MASTERS, LeaveDetail.BAR_REVIEW],
    LeaveType.OTHER: PURPOSE,
}
# Details whose blank line asks the employee to specify (country, illness).
DETAILS_TO_SPECIFY = {LeaveDetail.ABROAD, LeaveDetail.IN_HOSPITAL, LeaveDetail.OUT_PATIENT, LeaveDetail.WOMEN_ILLNESS}


class LeaveError(Exception):
    """A leave action that is not allowed; the message is shown to the user."""


# --- Days and balances ----------------------------------------------------------------


def holidays_between(start, end):
    """Municipality-wide holidays from the calendar (C1), as a set of dates."""
    days = set()
    events = CalendarEvent.objects.filter(
        kind=CalendarEvent.Kind.HOLIDAY, office__isnull=True, start_date__lte=end,
    ).filter(Q(end_date__gte=start) | Q(end_date__isnull=True, start_date__gte=start))
    for event in events:
        day = max(event.start_date, start)
        while day <= min(event.end_date or event.start_date, end):
            days.add(day)
            day += timedelta(days=1)
    return days


def working_days(start, end):
    """Weekdays from start to end, less holidays on the calendar (L4)."""
    if not start or not end or end < start:
        return 0
    off = holidays_between(start, end)
    count, day = 0, start
    while day <= end:
        if day.weekday() < 5 and day not in off:
            count += 1
        day += timedelta(days=1)
    return count


def balance(person, leave_type, as_of=None):
    rows = LeaveCredit.objects.filter(person=person, leave_type=leave_type)
    if as_of:
        rows = rows.filter(as_of__lte=as_of)
    return rows.aggregate(total=Sum("days"))["total"] or Decimal("0")


def _held(person, charge, exclude=None):
    """Days of pending requests that will be charged to this ledger once approved."""
    types = [t for t, c in CHARGED_TO.items() if c == charge]
    pending = LeaveRequest.objects.filter(person=person, status=LeaveRequest.Status.PENDING, leave_type__in=types)
    if exclude is not None:
        pending = pending.exclude(pk=exclude.pk)
    total = Decimal("0")
    for req in pending:
        total += req.days_with_pay if req.days_with_pay is not None else req.days
    return total


def spl_used(person, year, exclude=None):
    used = LeaveRequest.objects.filter(
        person=person, leave_type=LeaveType.SPECIAL_PRIVILEGE, start_date__year=year,
        status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED],
    )
    if exclude is not None:
        used = used.exclude(pk=exclude.pk)
    return used.aggregate(total=Sum("days"))["total"] or Decimal("0")


def summary(person, today=None):
    """Everything the employee's leave card shows: balances, what is held by pending requests,
    forced leave still to take this year, and special privilege leave left (L5, L8)."""
    today = today or timezone.localdate()
    vl, sl = balance(person, LeaveType.VACATION), balance(person, LeaveType.SICK)
    vl_held, sl_held = _held(person, LeaveType.VACATION), _held(person, LeaveType.SICK)
    taken = LeaveRequest.objects.filter(
        person=person, status=LeaveRequest.Status.APPROVED, start_date__year=today.year,
        leave_type__in=[LeaveType.VACATION, LeaveType.MANDATORY],
    ).aggregate(total=Sum("days"))["total"] or Decimal("0")
    forced_left = max(FORCED_LEAVE_DAYS - taken, Decimal("0")) if vl >= FORCED_LEAVE_THRESHOLD else Decimal("0")
    spl_left = max(SPL_DAYS_PER_YEAR - spl_used(person, today.year), Decimal("0"))
    from . import coc

    coc_hours = coc.balance(person, today)
    return {
        "coc_hours": coc_hours,
        "coc_held": coc.held_hours(person),
        "coc_expiring": coc.expiring(person, today),
        "earns_coc": coc.earns_coc(person),
        "eligible": person.earns_leave_credits(),
        "vl": vl, "sl": sl, "total": vl + sl,
        "vl_held": vl_held, "sl_held": sl_held,
        "vl_available": vl - vl_held, "sl_available": sl - sl_held,
        "forced_left": forced_left, "forced_taken": taken,
        "spl_left": spl_left, "spl_allowance": SPL_DAYS_PER_YEAR,
        "year_end": date(today.year, 12, 31),
        "pending": LeaveRequest.objects.filter(person=person, status=LeaveRequest.Status.PENDING).count(),
    }


def pay_split(req):
    """Days with and without pay (7.C), from what is available after other pending requests."""
    days = req.days
    charge = CHARGED_TO.get(req.leave_type)
    if charge == LeaveType.VACATION:
        available = balance(req.person, LeaveType.VACATION) - _held(req.person, LeaveType.VACATION, exclude=req)
    elif charge == LeaveType.SICK:
        # Sick leave beyond the sick leave balance is charged to vacation leave.
        available = (balance(req.person, LeaveType.SICK) - _held(req.person, LeaveType.SICK, exclude=req)) + max(
            balance(req.person, LeaveType.VACATION) - _held(req.person, LeaveType.VACATION, exclude=req), Decimal("0")
        )
    elif req.leave_type == LeaveType.SPECIAL_PRIVILEGE:
        available = SPL_DAYS_PER_YEAR - spl_used(req.person, req.start_date.year, exclude=req)
    elif req.leave_type == LeaveType.CTO:
        from . import coc

        available = coc.available_days(req.person, req.start_date, exclude=req)
    else:
        return days, Decimal("0")
    with_pay = min(days, max(available, Decimal("0")))
    return with_pay, days - with_pay


def check_filing(req, today):
    """Warnings, not blocks: HRMO and the approvers decide (L7)."""
    warnings = []
    ahead = (req.start_date - today).days
    if req.leave_type == LeaveType.VACATION and ahead < 5:
        warnings.append("Vacation leave is normally filed at least 5 days ahead.")
    if req.leave_type == LeaveType.SPECIAL_PRIVILEGE and ahead < 7:
        warnings.append("Special privilege leave is normally filed a week ahead, except in emergencies.")
    if req.leave_type == LeaveType.SICK and (req.days > 5 or req.start_date > today):
        warnings.append("Bring a medical certificate: sick leave over 5 days or filed ahead needs one.")
    with_pay, without_pay = pay_split(req)
    if without_pay > 0 and req.leave_type == LeaveType.CTO:
        warnings.append(f"Not enough overtime credits (COC): about {without_pay} day(s) are not covered.")
    elif without_pay > 0 and req.leave_type in (*CHARGED_TO, LeaveType.SPECIAL_PRIVILEGE):
        warnings.append(f"Not enough credits: about {without_pay} day(s) would be without pay.")
    overlap = LeaveRequest.objects.filter(
        person=req.person, status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED],
        start_date__lte=req.end_date, end_date__gte=req.start_date,
    ).exclude(pk=req.pk)
    if overlap.exists():
        warnings.append("You already have leave filed on some of these days.")
    return warnings


def office_on_leave(req):
    """Other people of the same office on leave on any of the same days, for the office head (L8)."""
    if not req.office_id:
        return []
    active = Engagement.objects.filter(office_id=req.office_id, status=Engagement.Status.ACTIVE).values("person_id")
    return list(LeaveRequest.objects.filter(
        person__in=active, status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED],
        start_date__lte=req.end_date, end_date__gte=req.start_date,
    ).exclude(person_id=req.person_id).select_related("person").order_by("start_date"))


# --- Filing and the approval chain --------------------------------------------------------


def current_engagement(person):
    return (person.engagements.filter(status=Engagement.Status.ACTIVE)
            .select_related("office").order_by("-start_date").first())


def _heads_office(person, office):
    """True when the applicant is an Office Head of the office the leave is filed in."""
    return UserProfile.objects.filter(
        person=person, office=office, user__groups__name=ROLE_OFFICE_HEAD
    ).exists()


def file_request(req, user):
    """Saves a new request and creates its approval steps in the configured order (L1)."""
    eng = current_engagement(req.person)
    if eng:
        req.office = eng.office
        req.position_title = eng.position_title
    req.filed_by = user
    req.status = LeaveRequest.Status.PENDING
    req.save()
    kinds = [k for k in settings.LEAVE_APPROVAL_STEPS if k in Step.Kind.values] or ["recommend", "certify", "approve"]
    for order, kind in enumerate(kinds, start=1):
        step = Step(request=req, order=order, kind=kind)
        if kind == Step.Kind.RECOMMEND and req.office and _heads_office(req.person, req.office):
            step.status = Step.Status.SKIPPED
            step.note = "The applicant heads the office."
        step.save()
    return req


def _is_applicant(user, req):
    profile = getattr(user, "profile", None)
    return bool(profile and profile.person_id == req.person_id)


def can_act(user, step):
    """(may act, only by recording the paper form). Nobody acts on their own leave (L6)."""
    req = step.request
    if not user.is_authenticated or _is_applicant(user, req) or step.status != Step.Status.PENDING:
        return False, False
    if user.is_superuser:
        return True, False
    if step.kind == Step.Kind.RECOMMEND:
        profile = getattr(user, "profile", None)
        if is_office_head(user) and profile and req.office_id and profile.office_id == req.office_id:
            return True, False
    elif step.kind == Step.Kind.CERTIFY:
        if is_hrmo(user):
            return True, False
    elif step.kind == Step.Kind.APPROVE:
        if is_executive(user):
            return True, False
    if is_hrmo(user):
        return True, True
    return False, False


def act(req, user, decision, note="", with_pay=None, without_pay=None):
    """Signs the current step: decision is "done" or "disapproved". Call inside a transaction
    with `req` locked; the caller bumps the version and saves `req`."""
    step = req.current_step
    if step is None:
        raise LeaveError("This application is no longer waiting for anyone.")
    allowed, paper_only = can_act(user, step)
    if not allowed:
        raise LeaveError("It is not your turn to act on this application.")
    if decision == "disapproved" and not note:
        raise LeaveError("Give the reason for disapproval; it is printed on the form.")
    now = timezone.now()
    step.acted_by, step.acted_at, step.note, step.from_paper = user, now, note, paper_only
    if step.kind == Step.Kind.CERTIFY and decision == "done":
        auto_with, auto_without = pay_split(req)
        if with_pay is None:
            with_pay, without_pay = auto_with, auto_without
        if with_pay < 0 or without_pay < 0 or with_pay + without_pay != req.days:
            raise LeaveError(f"Days with pay and without pay must add up to {req.days}.")
        req.days_with_pay, req.days_without_pay = with_pay, without_pay
        req.certified_balances = {
            "as_of": timezone.localdate().isoformat(),
            "vl": str(balance(req.person, LeaveType.VACATION)),
            "sl": str(balance(req.person, LeaveType.SICK)),
        }
    if decision == "disapproved":
        step.status = Step.Status.DISAPPROVED
        step.save()
        req.status = LeaveRequest.Status.REJECTED
        req.decision_note = note
        req.decided_at = now
        return step
    if decision != "done":
        raise LeaveError("Unknown decision.")
    step.status = Step.Status.DONE
    step.save()
    if not req.steps.filter(status=Step.Status.PENDING).exists():
        _approve(req, user, now)
    return step


def _approve(req, user, now):
    """Final approval: charges the ledger for the days with pay (L2, L5)."""
    if req.days_with_pay is None:
        req.days_with_pay, req.days_without_pay = pay_split(req)
    req.status = LeaveRequest.Status.APPROVED
    req.approver = user
    req.decided_at = now
    if req.leave_type == LeaveType.CTO:
        from . import coc

        coc.charge(req, user)
        return
    charge = CHARGED_TO.get(req.leave_type)
    if not charge or not req.days_with_pay:
        return
    remaining = req.days_with_pay
    order = [LeaveType.SICK, LeaveType.VACATION] if charge == LeaveType.SICK else [LeaveType.VACATION]
    for ledger in order:
        if remaining <= 0:
            break
        part = remaining if ledger == order[-1] else min(remaining, max(balance(req.person, ledger), Decimal("0")))
        if part > 0:
            LeaveCredit.objects.create(
                person=req.person, leave_type=ledger, days=-part, as_of=req.start_date,
                note=f"Approved {req.get_leave_type_display().lower()} #{req.pk}", leave_request=req,
            )
            remaining -= part


def cancel(req, user, note=""):
    """The applicant withdraws a pending request; HRMO may also cancel an approved one, which puts
    the charged days back on the ledger (L9)."""
    if req.status == LeaveRequest.Status.PENDING:
        if not (_is_applicant(user, req) or is_hrmo(user) or user.is_superuser):
            raise LeaveError("Only the applicant or HRMO can withdraw this application.")
    elif req.status == LeaveRequest.Status.APPROVED:
        if not (is_hrmo(user) or user.is_superuser):
            raise LeaveError("Ask HRMO to cancel approved leave.")
        if not note:
            raise LeaveError("Give the reason for cancelling approved leave.")
        for entry in req.credit_entries.all():
            LeaveCredit.objects.create(
                person=req.person, leave_type=entry.leave_type, days=-entry.days, as_of=timezone.localdate(),
                note=f"Restored: cancelled leave #{req.pk}", leave_request=req,
            )
        if req.leave_type == LeaveType.CTO:
            from . import coc

            coc.restore(req)
    else:
        raise LeaveError("This application is already closed.")
    for step in req.steps.filter(status=Step.Status.PENDING):
        step.status = Step.Status.SKIPPED
        step.note = "Cancelled."
        step.save()
    req.status = LeaveRequest.Status.CANCELLED
    req.decision_note = note or "Withdrawn by the applicant."
    req.decided_at = timezone.now()


def inbox(user):
    """Pending requests whose current step this user may sign in their own name."""
    pending = (LeaveRequest.objects.filter(status=LeaveRequest.Status.PENDING)
               .select_related("person", "office").prefetch_related("steps").order_by("start_date"))
    profile = getattr(user, "profile", None)
    own_person = profile.person_id if profile else None
    if user.is_superuser:
        kinds = set(Step.Kind.values)
    else:
        # Roles looked up once, not per application: this runs for the menu count on every page.
        kinds = set()
        if is_office_head(user) and profile and profile.office_id:
            kinds.add(Step.Kind.RECOMMEND)
        if is_hrmo(user):
            kinds.add(Step.Kind.CERTIFY)
        if is_executive(user):
            kinds.add(Step.Kind.APPROVE)
        if not kinds:
            return []
        pending = pending.filter(steps__status=Step.Status.PENDING, steps__kind__in=kinds).distinct()
    rows = []
    for req in pending:
        step = req.current_step
        if not step or step.kind not in kinds or req.person_id == own_person:
            continue
        if step.kind == Step.Kind.RECOMMEND and not user.is_superuser and req.office_id != profile.office_id:
            continue
        rows.append(req)
    return rows


# --- Ledger ---------------------------------------------------------------------------


def eligible_people():
    return Person.objects.filter(
        engagements__status=Engagement.Status.ACTIVE, engagements__leave_eligible=True
    ).distinct().order_by("last_name", "first_name")


def earning_note(year, month):
    return f"Earned {date(year, month, 1):%B %Y}"


def post_monthly_credits(year, month, dry_run=False):
    """Adds 1.25 days vacation and 1.25 days sick leave for the month to every leave-eligible person
    who has not had that month posted yet. HRMO presses it; it never runs on its own (L2)."""
    note = earning_note(year, month)
    as_of = date(year, month, monthrange(year, month)[1])
    done = set(LeaveCredit.objects.filter(note=note).values_list("person_id", flat=True))
    people = [p for p in eligible_people() if p.pk not in done]
    if not dry_run:
        for person in people:
            for leave_type in (LeaveType.VACATION, LeaveType.SICK):
                LeaveCredit.objects.create(person=person, leave_type=leave_type, days=MONTHLY_EARNING,
                                           as_of=as_of, note=note)
    return people


def balances_board(as_of, office=None):
    """Rows of the "Leave balances as of" list HRMO posts on the bulletin board."""
    people = eligible_people()
    if office:
        people = people.filter(engagements__office=office, engagements__status=Engagement.Status.ACTIVE).distinct()
    totals = {}
    for row in (LeaveCredit.objects.filter(person__in=people, as_of__lte=as_of,
                                           leave_type__in=[LeaveType.VACATION, LeaveType.SICK])
                .values("person_id", "leave_type").annotate(total=Sum("days"))):
        totals[(row["person_id"], row["leave_type"])] = row["total"]
    rows = []
    for person in people:
        vl = totals.get((person.pk, LeaveType.VACATION), Decimal("0"))
        sl = totals.get((person.pk, LeaveType.SICK), Decimal("0"))
        rows.append({"person": person, "vl": vl, "sl": sl, "total": vl + sl})
    return rows
