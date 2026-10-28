"""Pass slips: permission to leave the office during working hours, signed in the configured order
(DECISIONS PS1-PS5). Built like the leave chain (L1): the slip is filed online or entered from paper,
each signature is a step, and the printout keeps the names of whoever signed online (F1).
"""

from datetime import date

from django.conf import settings
from django.utils import timezone

from .leave import _heads_office, current_engagement
from .models import PassSlip, PassSlipStep
from .permissions import is_executive, is_hrmo, is_office_head

Step = PassSlipStep
DEFAULT_STEPS = ["note", "approve"]


class SlipError(Exception):
    """A pass slip action that is not allowed; the message is shown to the user."""


def step_kinds():
    kinds = [k for k in settings.PASS_SLIP_STEPS if k in Step.Kind.values]
    return kinds if Step.Kind.APPROVE in kinds else DEFAULT_STEPS


def file_slip(slip, user):
    """Saves a new slip and its signature steps (PS1). The office head's step is skipped when the
    person heads the office."""
    eng = current_engagement(slip.person)
    if eng:
        slip.office = eng.office
        slip.position_title = eng.position_title
    slip.filed_by = user
    slip.status = PassSlip.Status.PENDING
    slip.save()
    for order, kind in enumerate(step_kinds(), start=1):
        step = Step(slip=slip, order=order, kind=kind)
        if kind == Step.Kind.RECOMMEND and slip.office and _heads_office(slip.person, slip.office):
            step.status, step.note = Step.Status.SKIPPED, "The employee heads the office."
        step.save()
    return slip


def _is_person(user, slip):
    profile = getattr(user, "profile", None)
    return bool(profile and profile.person_id == slip.person_id)


def can_act(user, step):
    """(may act, only by recording the paper slip). Nobody signs their own slip (PS2)."""
    slip = step.slip
    if not user.is_authenticated or _is_person(user, slip) or step.status != Step.Status.PENDING:
        return False, False
    if user.is_superuser:
        return True, False
    if step.kind == Step.Kind.RECOMMEND:
        profile = getattr(user, "profile", None)
        if is_office_head(user) and profile and slip.office_id and profile.office_id == slip.office_id:
            return True, False
    elif step.kind == Step.Kind.NOTE and is_hrmo(user):
        return True, False
    elif step.kind == Step.Kind.APPROVE and is_executive(user):
        return True, False
    if is_hrmo(user):
        return True, True
    return False, False


def act(slip, user, decision, note=""):
    """Signs the current step. Call inside a transaction with `slip` locked; the caller bumps the
    version and saves it."""
    step = slip.current_step
    if step is None:
        raise SlipError("This pass slip is no longer waiting for anyone.")
    allowed, paper_only = can_act(user, step)
    if not allowed:
        raise SlipError("It is not your turn to sign this pass slip.")
    if decision == "disapproved" and not note:
        raise SlipError("Give the reason for disapproval.")
    if decision not in ("done", "disapproved"):
        raise SlipError("Unknown decision.")
    now = timezone.now()
    step.acted_by, step.acted_at, step.note, step.from_paper = user, now, note, paper_only
    step.status = Step.Status.DONE if decision == "done" else Step.Status.DISAPPROVED
    step.save()
    if decision == "disapproved":
        slip.status, slip.decision_note, slip.decided_at = PassSlip.Status.DISAPPROVED, note, now
    elif not slip.steps.filter(status=Step.Status.PENDING).exists():
        slip.status, slip.decided_at = PassSlip.Status.APPROVED, now
    return step


def cancel(slip, user):
    """The person withdraws a slip before they go out; HRMO can cancel any open one (PS2)."""
    if slip.status not in (PassSlip.Status.PENDING, PassSlip.Status.APPROVED) or slip.actual_out:
        raise SlipError("This pass slip can no longer be cancelled.")
    if not (_is_person(user, slip) or is_hrmo(user) or user.is_superuser):
        raise SlipError("Only the employee or HRMO can cancel this pass slip.")
    for step in slip.steps.filter(status=Step.Status.PENDING):
        step.status, step.note = Step.Status.SKIPPED, "Cancelled."
        step.save()
    slip.status, slip.decided_at = PassSlip.Status.CANCELLED, timezone.now()


def record_time(slip, user, which, when=None):
    """The person presses "I'm leaving now" / "I'm back"; HRMO may type the time from the logbook (PS3)."""
    if slip.status != PassSlip.Status.APPROVED:
        raise SlipError("The pass slip must be approved first.")
    hrmo = (is_hrmo(user) or user.is_superuser) and not _is_person(user, slip)
    if not (_is_person(user, slip) or hrmo):
        raise SlipError("Only the employee or HRMO records the time.")
    if when is not None and not hrmo:
        raise SlipError("Only HRMO corrects the time.")
    now = timezone.localtime()
    if when is None:
        if slip.slip_date != now.date():
            raise SlipError("Times are recorded on the day of the pass slip. Ask HRMO to correct them.")
        when = now.time().replace(second=0, microsecond=0)
    if which == "out":
        slip.actual_out = when
    elif which == "back":
        if not slip.actual_out:
            raise SlipError("Record the time out first.")
        if when <= slip.actual_out:
            raise SlipError("The time back must be after the time out.")
        slip.actual_back = when
    else:
        raise SlipError("Unknown time.")


def inbox(user):
    """Pending slips whose current step this user may sign in their own name, oldest first."""
    profile = getattr(user, "profile", None)
    kinds = set(Step.Kind.values) if user.is_superuser else set()
    if not user.is_superuser:
        if is_office_head(user) and profile and profile.office_id:
            kinds.add(Step.Kind.RECOMMEND)
        if is_hrmo(user):
            kinds.add(Step.Kind.NOTE)
        if is_executive(user):
            kinds.add(Step.Kind.APPROVE)
    if not kinds:
        return []
    pending = (PassSlip.objects.filter(status=PassSlip.Status.PENDING, steps__status=Step.Status.PENDING,
                                       steps__kind__in=kinds).distinct()
               .select_related("person", "office").prefetch_related("steps").order_by("slip_date", "time_out"))
    own = profile.person_id if profile else None
    rows = []
    for slip in pending:
        step = slip.current_step
        if not step or step.kind not in kinds or slip.person_id == own:
            continue
        if step.kind == Step.Kind.RECOMMEND and not user.is_superuser and slip.office_id != profile.office_id:
            continue
        rows.append(slip)
    return rows


def month_log(year, month, office=None):
    """Approved slips in a month, with each person's personal time out added up (PS4)."""
    first = date(year, month, 1)
    last = date(year + (month == 12), month % 12 + 1, 1)
    slips = (PassSlip.objects.filter(status=PassSlip.Status.APPROVED, slip_date__gte=first, slip_date__lt=last)
             .select_related("person", "office").order_by("slip_date", "time_out"))
    if office:
        slips = slips.filter(office=office)
    slips = list(slips)
    totals = {}
    for slip in slips:
        row = totals.setdefault(slip.person_id, {"person": slip.person, "office": slip.office, "official": 0,
                                                 "personal": 0, "personal_minutes": 0, "not_back": 0})
        row[slip.kind] += 1
        if slip.kind == PassSlip.Kind.PERSONAL and slip.minutes_out:
            row["personal_minutes"] += slip.minutes_out
        if slip.actual_out and not slip.actual_back and slip.expected_back:
            row["not_back"] += 1
    return slips, sorted(totals.values(), key=lambda r: (-r["personal_minutes"], r["person"].last_name))
