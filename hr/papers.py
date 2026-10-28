"""Printed papers made from the appointments on file: CS Form No. 33, the oath, the certificate of
assumption, the position description, job order and contract of service papers, the ROAI, the JO/COS
list, and NOSA / NOSI notices from the salary schedules HRMO pastes in (DECISIONS AP1-AP6, JC1-JC4,
NS1-NS4). Paper stays the record (F1): every page prints for wet signatures.
"""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q

from .models import Engagement, SalaryAction, SalaryRate, SalarySchedule, Vacancy

# Appointment types that get CS Form No. 33 and appear on the ROAI (AP1). JO and COS are contracts,
# not appointments; elected officials take an oath but are not appointed.
APPOINTED_TYPES = [Engagement.Type.PLANTILLA, Engagement.Type.CASUAL, Engagement.Type.CONTRACTUAL]
CONTRACT_TYPES = [Engagement.Type.JOB_ORDER, Engagement.Type.CONTRACT_OF_SERVICE, Engagement.Type.PROJECT_BASED]
OATH_TYPES = APPOINTED_TYPES + [Engagement.Type.ELECTED]

# Printed on CS Form No. 33 as the employment status (AP1).
STATUS_WORDS = {
    Engagement.AppointmentStatus.PERMANENT: "Permanent",
    Engagement.AppointmentStatus.TEMPORARY: "Temporary",
    Engagement.AppointmentStatus.COTERMINOUS: "Coterminous",
}

# Terminal leave benefit = highest monthly salary x leave credits x this factor (SE4).
TERMINAL_LEAVE_FACTOR = Decimal("0.0481927")


# --- Words and money ---------------------------------------------------------------------------

_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_thousand(n):
    words = []
    if n >= 100:
        words += [_ONES[n // 100], "Hundred"]
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        words.append(_ONES[n])
    return " ".join(words)


def number_in_words(n):
    """Whole number in words, as written on appointments and notices: 25,439 -> Twenty-Five Thousand ..."""
    n = int(n)
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10**9, "Billion"), (10**6, "Million"), (1000, "Thousand"), (1, "")):
        if n >= size:
            chunk, n = divmod(n, size)
            parts.append(f"{_below_thousand(chunk)} {name}".strip())
    return " ".join(parts)


def amount_in_words(amount):
    """₱ amount in words with centavos as a fraction: "Twenty Thousand Pesos and 50/100"."""
    if amount is None:
        return ""
    amount = Decimal(amount).quantize(Decimal("0.01"))
    pesos = int(amount)
    centavos = int((amount - pesos) * 100)
    words = f"{number_in_words(pesos)} Peso{'s' if pesos != 1 else ''}"
    return f"{words} and {centavos:02d}/100" if centavos else f"{words} Only"


def peso(amount):
    return "" if amount is None else f"₱{Decimal(amount):,.2f}"


def ordinal_day(day):
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def honorific(person):
    return {"M": "Mr.", "F": "Ms."}.get(person.sex, "Mr./Ms.")


def signatories():
    """Names under the signature lines; blank prints an empty line (AP3)."""
    return {
        "mayor": settings.LEAVE_FORM_MAYOR,
        "hrmo": settings.CERTIFICATE_SIGNATORY,
        "hrmo_title": settings.CERTIFICATE_SIGNATORY_TITLE,
        "accountant": settings.CONTRACT_ACCOUNTANT,
    }


# --- Appointment papers (AP1-AP4) ---------------------------------------------------------------


def monthly_rate(eng):
    if eng.rate is None:
        return None
    if eng.rate_basis == Engagement.RateBasis.MONTHLY:
        return eng.rate
    if eng.rate_basis == Engagement.RateBasis.DAILY:
        return (eng.rate * settings.WORKING_DAYS_PER_MONTH).quantize(Decimal("0.01"))
    return None


def publication_for(eng):
    """The vacancy notice for the same plantilla item, published before the appointment (AP2)."""
    if not eng.item_number:
        return None
    issued = eng.issued_on or eng.start_date
    return (Vacancy.objects.filter(item_number__iexact=eng.item_number, published_on__isnull=False,
                                   published_on__lte=issued)
            .exclude(status=Vacancy.Status.CANCELLED).order_by("-published_on").first())


def employment_status(eng):
    if eng.type == Engagement.Type.PLANTILLA:
        return STATUS_WORDS.get(eng.appointment_status, "Permanent")
    return eng.get_type_display()


def appointment_context(eng):
    rate = monthly_rate(eng)
    return {
        "eng": eng, "person": eng.person, "honorific": honorific(eng.person),
        "status": employment_status(eng),
        "monthly": rate, "monthly_words": amount_in_words(rate), "monthly_peso": peso(rate),
        "vacancy": publication_for(eng),
        "form_label": settings.APPOINTMENT_FORM_LABEL,
        "supervisor": eng.reports_to,
        "sign": signatories(),
    }


def missing_for_appointment(eng):
    """What HRMO should fill in before printing CS Form No. 33; printing still works without it."""
    missing = []
    if not eng.nature:
        missing.append("nature of appointment")
    if eng.type == Engagement.Type.PLANTILLA and not eng.vice_name and not eng.vice_reason:
        missing.append("vice (who held the item before), or \"new item\" as the reason")
    if eng.rate is None:
        missing.append("monthly rate")
    return missing


# --- Report on Appointments Issued (AP5) -------------------------------------------------------


def roai_rows(year, month):
    """Appointments issued in a month: by the date the appointing officer signed, or by the start date
    when that date was not recorded (flagged on the report)."""
    first = date(year, month, 1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    qs = (Engagement.objects.filter(type__in=APPOINTED_TYPES)
          .filter(Q(issued_on__range=(first, last)) | Q(issued_on__isnull=True, start_date__range=(first, last)))
          .select_related("person", "office").order_by("issued_on", "start_date", "person__last_name"))
    rows = []
    for eng in qs:
        vacancy = publication_for(eng) if eng.type == Engagement.Type.PLANTILLA else None
        rows.append({
            "eng": eng, "person": eng.person,
            "issued": eng.issued_on or eng.start_date, "issued_guess": eng.issued_on is None,
            "grade": f"SG {eng.salary_grade}" if eng.salary_grade else "",
            "monthly": monthly_rate(eng), "status": employment_status(eng),
            "period": (eng.start_date, eng.end_date) if eng.type != Engagement.Type.PLANTILLA
                      or eng.appointment_status == Engagement.AppointmentStatus.TEMPORARY else None,
            "nature": eng.get_nature_display() if eng.nature else "",
            "vacancy": vacancy,
        })
    return rows


# --- Job orders and contracts of service (JC1-JC4) ---------------------------------------------


def fund_source(eng):
    if eng.project_id:
        ref = f" ({eng.project.funding_reference})" if eng.project.funding_reference else ""
        return f"{eng.project.name}{ref}"
    return settings.CONTRACT_FUND_SOURCE


def contract_context(eng):
    rate = eng.rate
    return {
        "eng": eng, "person": eng.person, "honorific": honorific(eng.person),
        "title": "JOB ORDER" if eng.type == Engagement.Type.JOB_ORDER else "CONTRACT OF SERVICE",
        "rate": rate, "rate_peso": peso(rate), "rate_words": amount_in_words(rate),
        "basis": {"daily": "per day", "monthly": "per month", "lump_sum": "for the whole contract"}.get(
            eng.rate_basis, ""),
        "fund": fund_source(eng),
        "sign": signatories(),
    }


def semester_bounds(year, half):
    return (date(year, 1, 1), date(year, 6, 30)) if half == 1 else (date(year, 7, 1), date(year, 12, 31))


def jo_cos_rows(start, end, office=None):
    """Job order, contract of service and project workers engaged at any time between two dates."""
    qs = (Engagement.objects.filter(type__in=CONTRACT_TYPES, start_date__lte=end)
          .filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
          .select_related("person", "office", "project").order_by("office__name", "person__last_name"))
    if office:
        qs = qs.filter(office=office)
    return list(qs)


# --- Salary schedules, NOSA and NOSI (NS1-NS4) ------------------------------------------------


class ScheduleError(Exception):
    """The pasted schedule could not be read; the message says which line."""


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


# A monthly salary is at least this much; smaller numbers on a line are grade or step labels.
MIN_AMOUNT = Decimal("1000")


def parse_schedule(text):
    """Reads a pasted salary table: one line per salary grade, eight monthly amounts (steps 1-8).
    A line may start with its grade; otherwise lines are grades 1, 2, 3 in order. Lines without any
    amount, such as the "Step 1 ... Step 8" header, are skipped. Returns {(grade, step): Decimal}."""
    cells, grade = {}, 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        try:
            values = [Decimal(n.replace(",", "")) for n in _NUMBER.findall(line)]
        except InvalidOperation:
            raise ScheduleError(f"Line {lineno}: could not read the amounts.")
        amounts = [v for v in values if v >= MIN_AMOUNT]
        if not amounts:
            continue
        if len(amounts) != 8:
            raise ScheduleError(f"Line {lineno}: expected 8 amounts (steps 1 to 8), found {len(amounts)}.")
        label = values[0] if values[0] < MIN_AMOUNT else None
        if label is not None and label == int(label) and 1 <= label <= 33:
            grade = int(label)
        else:
            grade += 1
        if grade > 33:
            raise ScheduleError(f"Line {lineno}: more than 33 salary grades.")
        if (grade, 1) in cells:
            raise ScheduleError(f"Line {lineno}: salary grade {grade} appears twice.")
        for step, value in enumerate(amounts, start=1):
            cells[(grade, step)] = value
    if not cells:
        raise ScheduleError("Paste the table: one line per salary grade with the 8 step amounts.")
    return cells


def schedule_warnings(cells):
    """Amounts that go down from one step or grade to the next: usually a pasting mistake."""
    warnings = []
    for (grade, step), value in sorted(cells.items()):
        if step > 1 and value < cells.get((grade, step - 1), 0):
            warnings.append(f"SG {grade}: step {step} is less than step {step - 1}.")
        if (grade - 1, step) in cells and value < cells[(grade - 1, step)]:
            warnings.append(f"SG {grade} step {step} is less than SG {grade - 1} step {step}.")
    return warnings


def schedule_on(day):
    """The schedule in force on a day: the latest one that took effect on or before it."""
    return SalarySchedule.objects.filter(effective_date__lte=day).order_by("-effective_date").first()


def rate_in(schedule, grade, step):
    if not schedule or not grade or not step:
        return None
    cell = SalaryRate.objects.filter(schedule=schedule, salary_grade=grade, step=step).first()
    return cell.monthly if cell else None


def grid(schedule):
    """Rows of (grade, [8 amounts or None]) for showing a schedule."""
    cells = {(r.salary_grade, r.step): r.monthly for r in schedule.rates.all()}
    grades = sorted({g for g, _s in cells})
    return [(g, [cells.get((g, s)) for s in range(1, 9)]) for g in grades]


def nosa_rows(schedule, office=None):
    """One NOSA per active plantilla appointment: the actual salary the day before the schedule took
    effect against the new schedule's amount for the same grade and step (NS2)."""
    previous = schedule_on(schedule.effective_date - timedelta(days=1))
    qs = (Engagement.objects.filter(type=Engagement.Type.PLANTILLA, status=Engagement.Status.ACTIVE,
                                    start_date__lte=schedule.effective_date)
          .select_related("person", "office").order_by("office__name", "person__last_name"))
    if office:
        qs = qs.filter(office=office)
    new_cells = {(r.salary_grade, r.step): r.monthly for r in schedule.rates.all()}
    rows = []
    for eng in qs:
        old = rate_in(previous, eng.salary_grade, eng.step) or monthly_rate(eng)
        new = new_cells.get((eng.salary_grade, eng.step))
        rows.append({
            "eng": eng, "person": eng.person, "honorific": honorific(eng.person),
            "old": old, "new": new, "diff": (new - old) if (new is not None and old is not None) else None,
            "stored": monthly_rate(eng),
        })
    return rows, previous


def nosi_context(case):
    """The NOSI for a step increment case: the salary at the old step and the new one, from the schedule
    in force on the effective date, falling back to the stored rate for the old step (NS3)."""
    eng = case.engagement
    effective = case.effective_date or case.due_date
    schedule = schedule_on(effective)
    old = rate_in(schedule, eng.salary_grade, case.from_step)
    if old is None and eng.step == case.from_step:
        old = monthly_rate(eng)
    new = rate_in(schedule, eng.salary_grade, case.to_step)
    return {
        "case": case, "eng": eng, "person": case.person, "honorific": honorific(case.person),
        "effective": effective, "day_before": effective - timedelta(days=1), "schedule": schedule,
        "old": old, "new": new, "diff": (new - old) if (new is not None and old is not None) else None,
        "steps": case.to_step - case.from_step, "sign": signatories(),
    }


def can_print_nosi(case):
    return case.kind == SalaryAction.Kind.STEP_INCREMENT and case.status in (
        SalaryAction.Status.REVIEW, SalaryAction.Status.SIGNATURE, SalaryAction.Status.ISSUED)
