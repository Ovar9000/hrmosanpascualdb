"""Salary actions tracker: finds step increments and loyalty awards that are due (DECISIONS SA1-SA6).

The check is a pure function of the records (appointments, leave without pay and suspensions,
earlier cases), so it can run every day, from a button, or twice in a row without creating
duplicates. It never decides entitlement: it dates the milestone, lists what HRMO must still
verify, and opens a case. Only an Issued step increment changes pay data (Engagement.step).
"""

from datetime import date, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import Engagement, Person, SalaryAction, SalaryCheckRun

STEP_YEARS = 3
MAX_STEP = 8
LOYALTY_FIRST, LOYALTY_EVERY, LOYALTY_LAST = 10, 5, 60
# Appointive government service. JO and COS are not government service; elected terms are
# not counted for the CSC loyalty award (SA2, unconfirmed).
LOYALTY_TYPES = [Engagement.Type.PLANTILLA, Engagement.Type.CASUAL, Engagement.Type.CONTRACTUAL]
NOT_COUNTED_TYPES = [Engagement.Type.JOB_ORDER, Engagement.Type.CONTRACT_OF_SERVICE, Engagement.Type.PROJECT_BASED]

CHECKLISTS = {
    SalaryAction.Kind.STEP_INCREMENT: [
        ("rating", "Performance ratings for the whole period are at least Satisfactory"),
        ("no_suspension", "No suspension or administrative penalty in the period"),
        ("lwop", "All leave without pay in the period is recorded on this page (or there was none)"),
        ("funds", "Funds are available within the personal services limit"),
    ],
    SalaryAction.Kind.LOYALTY: [
        ("rating", "Performance ratings for the whole period are at least Satisfactory"),
        ("no_suspension", "No suspension or administrative penalty in the period"),
        ("service_record", "Service record confirms continuous service in the LGU since the counting date"),
    ],
}


def tracking_start():
    return date.fromisoformat(settings.SALARY_TRACKING_START)


def add_years(day, years):
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def _overlap_days(interruptions, start, end):
    total = 0
    for item in interruptions:
        first, last = max(item.start_date, start), min(item.end_date, end)
        if last >= first:
            total += (last - first).days + 1
    return total


def extended_due(start, years, interruptions):
    """start + years, pushed back by every leave-without-pay or suspension day inside the period."""
    extension = 0
    for _ in range(20):
        due = add_years(start, years) + timedelta(days=extension)
        needed = _overlap_days(interruptions, start, due - timedelta(days=1))
        if needed == extension:
            break
        extension = needed
    return add_years(start, years) + timedelta(days=extension), extension


def _ends(eng, today):
    return eng.end_date or (today if eng.status == Engagement.Status.ACTIVE else eng.start_date)


def position_start(eng, engagements):
    """First day of the unbroken run of appointments to the same plantilla item that ends with eng."""
    start = eng.start_date
    earlier = sorted(
        (
            e for e in engagements
            if e.pk != eng.pk and e.type == Engagement.Type.PLANTILLA
            and e.item_number == eng.item_number and e.start_date < eng.start_date
        ),
        key=lambda e: e.start_date,
        reverse=True,
    )
    for prev in earlier:
        if prev.end_date is not None and prev.end_date + timedelta(days=1) < start:
            break
        start = min(start, prev.start_date)
    return start


def step_candidate(eng, engagements, interruptions, actions, today):
    """The next step increment of an active plantilla appointment, or None (SA1)."""
    if eng.status != Engagement.Status.ACTIVE or eng.type != Engagement.Type.PLANTILLA:
        return None
    if not eng.step or eng.step >= MAX_STEP:
        return None
    since = position_start(eng, engagements)
    warnings = []
    issued = [
        a.effective_date for a in actions
        if a.kind == SalaryAction.Kind.STEP_INCREMENT and a.status == SalaryAction.Status.ISSUED
        and a.effective_date and a.effective_date >= since
    ]
    if issued:
        cycle_start = max(issued)
        due, extension = extended_due(cycle_start, STEP_YEARS, interruptions)
    else:
        # Before go-live, increments were handled on paper: assume each 3-year cycle before
        # SALARY_TRACKING_START was granted, and date the first one after it.
        cycles = 1
        due, extension = extended_due(since, STEP_YEARS, interruptions)
        while due < tracking_start():
            cycles += 1
            due, extension = extended_due(since, STEP_YEARS * cycles, interruptions)
        cycle_start = add_years(since, STEP_YEARS * (cycles - 1))
        if cycles > 1 or eng.step > 1:
            warnings.append(
                f"No salary increase (step) is recorded here since this position started on {since:%b %d, %Y}. "
                f"Increments due before {tracking_start():%b %d, %Y} are assumed granted on paper: "
                "check the service record for the date of the last one."
            )
    if extension:
        warnings.append(f"Due date moved {extension} day(s) later for leave without pay or suspension.")
    return {
        "kind": SalaryAction.Kind.STEP_INCREMENT,
        "engagement": eng,
        "milestone": f"step:{eng.item_number}:{eng.step + 1}:{cycle_start.isoformat()}",
        "service_from": cycle_start,
        "due_date": due,
        "extension_days": extension,
        "from_step": eng.step,
        "to_step": eng.step + 1,
        "years": None,
        "warnings": warnings,
    }


def service_stretches(engagements, today):
    """Unbroken runs of loyalty-counted service, oldest first: [(start, end, [engagements])]."""
    counted = sorted((e for e in engagements if e.type in LOYALTY_TYPES), key=lambda e: e.start_date)
    stretches = []
    for eng in counted:
        end = _ends(eng, today)
        if stretches and eng.start_date <= stretches[-1][1] + timedelta(days=1):
            start, last_end, members = stretches[-1]
            stretches[-1] = (start, max(last_end, end), members + [eng])
        else:
            stretches.append((eng.start_date, end, [eng]))
    return stretches


def loyalty_candidate(engagements, interruptions, actions, today):
    """The next loyalty milestone of someone in unbroken appointive service, or None (SA2)."""
    stretches = service_stretches(engagements, today)
    if not stretches:
        return None
    start, _end, members = stretches[-1]
    current = [e for e in members if e.status == Engagement.Status.ACTIVE]
    if not current:
        return None
    closed = {
        a.milestone for a in actions
        if a.kind == SalaryAction.Kind.LOYALTY and a.status in SalaryAction.CLOSED_STATUSES
    }
    warnings = []
    for years in range(LOYALTY_FIRST, LOYALTY_LAST + 1, LOYALTY_EVERY):
        milestone = f"loyalty:{years}:{start.isoformat()}"
        due, extension = extended_due(start, years, interruptions)
        if milestone in closed:
            continue
        if due < tracking_start():
            warnings = [
                f"Loyalty milestones before {tracking_start():%b %d, %Y} are assumed handled on paper."
            ]
            continue
        break
    else:
        return None

    first_record = min(e.start_date for e in engagements)
    if start == first_record:
        warnings.append(
            f"The system has no appointments before {start:%b %d, %Y}. Earlier LGU service may count: "
            "check the service record."
        )
    if len(stretches) > 1:
        gap_from = stretches[-2][1]
        warnings.append(
            f"Service broke between {gap_from:%b %d, %Y} and {start:%b %d, %Y}, so counting starts again on {start:%b %d, %Y}."
        )
    not_counted = [e for e in engagements if e.type in NOT_COUNTED_TYPES and e.start_date < start]
    if not_counted:
        earliest = min(e.start_date for e in not_counted)
        warnings.append(f"Job Order, Contract of Service and project-based work since {earliest:%b %Y} is not counted.")
    if extension:
        warnings.append(f"Due date moved {extension} day(s) later for leave without pay or suspension.")
    return {
        "kind": SalaryAction.Kind.LOYALTY,
        "engagement": max(current, key=lambda e: e.start_date),
        "milestone": milestone,
        "service_from": start,
        "due_date": due,
        "extension_days": extension,
        "from_step": None,
        "to_step": None,
        "years": years,
        "warnings": warnings,
    }


def candidates_for(person, today):
    engagements = list(person.engagements.all())
    interruptions = list(person.service_interruptions.all())
    actions = list(person.salary_actions.all())
    found = []
    for eng in engagements:
        cand = step_candidate(eng, engagements, interruptions, actions, today)
        if cand:
            found.append(cand)
    cand = loyalty_candidate(engagements, interruptions, actions, today)
    if cand:
        found.append(cand)
    return found, actions


# Serializes runs (cron, deploy, page visits), so two never open the same case at once.
SALARY_CHECK_LOCK_KEY = 7_220_517
# How old the last run may be before a page visit runs the check itself (SA5).
STALE_AFTER = timedelta(hours=20)

SYNCED_FIELDS = ["engagement", "service_from", "due_date", "extension_days", "from_step", "to_step", "years", "warnings"]


def run_check(today=None, person_ids=None, source="command"):
    """Opens, re-dates and closes cases. Safe to run any number of times. Returns (created, updated)."""
    today = today or timezone.localdate()
    horizon = today + timedelta(days=settings.SALARY_LOOKAHEAD_DAYS)
    people = Person.objects.filter(
        pk__in=Engagement.objects.filter(status=Engagement.Status.ACTIVE).values("person_id")
    ) | Person.objects.filter(salary_actions__status__in=SalaryAction.AUTO_STATUSES)
    if person_ids is not None:
        people = people.filter(pk__in=person_ids)
    people = people.distinct().prefetch_related("engagements", "service_interruptions", "salary_actions")

    created = updated = 0
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [SALARY_CHECK_LOCK_KEY])
        for person in people:
            found, actions = candidates_for(person, today)
            by_key = {(a.kind, a.milestone): a for a in actions}
            wanted = set()
            for cand in found:
                key = (cand["kind"], cand["milestone"])
                wanted.add(key)
                case = by_key.get(key)
                if case is None:
                    if cand["due_date"] > horizon:
                        continue
                    status = SalaryAction.Status.DUE if cand["due_date"] <= today else SalaryAction.Status.UPCOMING
                    SalaryAction.objects.create(person=person, status=status, **cand)
                    created += 1
                elif case.status in SalaryAction.AUTO_STATUSES:
                    changed = [f for f in SYNCED_FIELDS if getattr(case, f) != cand[f]]
                    status = SalaryAction.Status.DUE if cand["due_date"] <= today else SalaryAction.Status.UPCOMING
                    if status != case.status:
                        changed.append("status")
                    if changed:
                        for field in SYNCED_FIELDS:
                            setattr(case, field, cand[field])
                        case.status = status
                        case.version += 1
                        case.save()
                        updated += 1
            # A case the check opened but which no longer applies (promotion, separation, step 8)
            # is closed with a note; cases HRMO has started are left for HRMO.
            for case in actions:
                if case.status in SalaryAction.AUTO_STATUSES and (case.kind, case.milestone) not in wanted:
                    case.status = SalaryAction.Status.NOT_ELIGIBLE
                    case.decision_note = (
                        f"Closed by the daily check on {today:%b %d, %Y}: the appointment or service changed, "
                        "so this milestone no longer applies."
                    )
                    case.version += 1
                    case.save()
                    updated += 1
            for case in actions:
                if case.status == SalaryAction.Status.DEFERRED and case.deferred_until and case.deferred_until <= today:
                    case.status = SalaryAction.Status.DUE
                    case.version += 1
                    case.save()
                    updated += 1
        SalaryCheckRun.objects.create(source=source, created=created, updated=updated)
    return created, updated


def last_run():
    return SalaryCheckRun.objects.order_by("-ran_at").first()


def run_if_stale(source):
    """Runs the check when it has never run or the last run is older than STALE_AFTER, so the
    tracker and calendar are current even without the daily job (SA5). Returns the last run."""
    last = last_run()
    if last is None or timezone.now() - last.ran_at > STALE_AFTER:
        run_check(source=source)
        last = last_run()
    return last


# --- Workflow (SA3-SA4) ------------------------------------------------------------


class WorkflowError(Exception):
    """A step HRMO tried that the case's current state does not allow."""


def checklist_items(case):
    return [(key, label, bool(case.checklist.get(key))) for key, label in CHECKLISTS[case.kind]]


def start_review(case):
    if case.status not in (SalaryAction.Status.UPCOMING, SalaryAction.Status.DUE, SalaryAction.Status.DEFERRED,
                           SalaryAction.Status.NOT_ELIGIBLE):
        raise WorkflowError("This case is already being worked on or is closed.")
    case.status = SalaryAction.Status.REVIEW
    case.deferred_until = None


def save_checklist(case, ticked):
    if case.status != SalaryAction.Status.REVIEW:
        raise WorkflowError("The checklist can only be changed while the case is under review.")
    case.checklist = {key: key in ticked for key, _label in CHECKLISTS[case.kind]}


def send_for_signature(case):
    if case.status != SalaryAction.Status.REVIEW:
        raise WorkflowError("Only a case under review can go for signature.")
    missing = [label for key, label, done in checklist_items(case) if not done]
    if missing:
        raise WorkflowError("Tick every checklist item first: " + "; ".join(missing) + ".")
    case.status = SalaryAction.Status.SIGNATURE


def defer(case, until, note, today):
    if case.status in SalaryAction.CLOSED_STATUSES:
        raise WorkflowError("A closed case cannot be deferred.")
    if until is None or until <= today:
        raise WorkflowError("Choose a date after today to bring the case back.")
    if not note:
        raise WorkflowError("Give a reason for deferring.")
    case.status = SalaryAction.Status.DEFERRED
    case.deferred_until = until
    case.decision_note = note


def mark_not_eligible(case, note):
    if case.status in SalaryAction.CLOSED_STATUSES:
        raise WorkflowError("This case is already closed.")
    if not note:
        raise WorkflowError("Give the reason, for the record.")
    case.status = SalaryAction.Status.NOT_ELIGIBLE
    case.decision_note = note


def issue(case, nosi_number, signed_on, effective_date, user):
    """Records the signed NOSI or award. A step increment also raises Engagement.step, under the
    same lock-and-version rule as every other edit (S2). Call inside transaction.atomic()."""
    if case.status != SalaryAction.Status.SIGNATURE:
        raise WorkflowError("Only a case awaiting signature can be issued.")
    if not nosi_number or not signed_on or not effective_date:
        raise WorkflowError("Enter the NOSI or award number, the date it was signed and the effective date.")
    if case.kind == SalaryAction.Kind.STEP_INCREMENT:
        eng = Engagement.objects.select_for_update().get(pk=case.engagement_id)
        if eng.status != Engagement.Status.ACTIVE:
            raise WorkflowError("The appointment is no longer active, so its step cannot change.")
        if eng.step != case.from_step:
            raise WorkflowError(
                f"The appointment is now at step {eng.step}, not {case.from_step}. Someone changed it; "
                "mark this case not eligible or review it again."
            )
        eng.step = case.to_step
        eng.version += 1
        eng.save()
    case.status = SalaryAction.Status.ISSUED
    case.nosi_number = nosi_number
    case.signed_on = signed_on
    case.effective_date = effective_date
    case.issued_by = user


def next_milestones(person, today=None):
    """For the person page: the next step increment and loyalty dates, open cases or not."""
    today = today or timezone.localdate()
    found, _actions = candidates_for(person, today)
    return {c["kind"]: c for c in found}
