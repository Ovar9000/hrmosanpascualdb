"""Service records and certificates of employment generated from the appointment history
(DECISIONS CR1-CR6). The printed, signed and sealed paper is the record (F1)."""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import CertificateRequest, Engagement, SalaryAction, ServiceInterruption

SEPARATION_CAUSES = {
    Engagement.Status.RESIGNED: "Resigned",
    Engagement.Status.RETIRED: "Retired",
    Engagement.Status.TERMINATED: "Terminated",
}

# A person may have this many requests being prepared or waiting for release at once (CR2).
MAX_OPEN_REQUESTS = 5


def excluded_types():
    """Appointment types left off the service record: job order and contract of service are not
    government service (CR1). Settable for the LGU's own reading."""
    return list(getattr(settings, "SERVICE_RECORD_EXCLUDED_TYPES", ["jo", "cos"]))


def salary_text(eng):
    """The stored rate as a service record writes it (D2: an input, not computed pay)."""
    if eng.rate is None:
        return ""
    if eng.rate_basis == Engagement.RateBasis.MONTHLY:
        return f"₱{eng.rate * 12:,.2f} p.a."
    if eng.rate_basis == Engagement.RateBasis.DAILY:
        return f"₱{eng.rate:,.2f} per day"
    return f"₱{eng.rate:,.2f} lump sum"


def status_text(eng):
    if eng.type == Engagement.Type.PLANTILLA and eng.appointment_status:
        return eng.get_appointment_status_display()
    return eng.get_type_display()


def _overlap_days(start, end, interruption):
    end = end or timezone.localdate()
    first, last = max(start, interruption.start_date), min(end, interruption.end_date)
    return max((last - first).days + 1, 0)


def service_rows(person):
    """One row per appointment, split where an issued step increment changed the salary, in date
    order. The stored rate is today's, so only an appointment's last row shows a salary (CR1)."""
    engs = list(person.engagements.exclude(type__in=excluded_types()).select_related("office")
                .order_by("start_date", "pk"))
    increments = defaultdict(list)
    for case in (SalaryAction.objects.filter(engagement__in=engs, kind=SalaryAction.Kind.STEP_INCREMENT,
                                             status=SalaryAction.Status.ISSUED, effective_date__isnull=False)
                 .order_by("effective_date")):
        increments[case.engagement_id].append(case)
    interruptions = list(person.service_interruptions.all())
    rows = []
    for index, eng in enumerate(engs):
        cases = [c for c in increments[eng.pk]
                 if eng.start_date < c.effective_date and (eng.end_date is None or c.effective_date <= eng.end_date)]
        step = cases[0].from_step if cases else eng.step
        start = eng.start_date
        segments = []
        for case in cases:
            segments.append((start, case.effective_date - timedelta(days=1), step))
            start, step = case.effective_date, case.to_step
        segments.append((start, eng.end_date, eng.step))
        later = engs[index + 1:]
        for number, (seg_start, seg_end, seg_step) in enumerate(segments):
            last = number == len(segments) - 1
            cause = ""
            if last and eng.end_date:
                cause = SEPARATION_CAUSES.get(eng.status, "")
                if not cause and eng.status == Engagement.Status.ENDED and not later:
                    cause = "End of term" if eng.type == Engagement.Type.ELECTED else "End of contract"
            lwop = sum(_overlap_days(seg_start, seg_end, i) for i in interruptions
                       if i.kind == ServiceInterruption.Kind.LWOP)
            suspended = sum(_overlap_days(seg_start, seg_end, i) for i in interruptions
                            if i.kind == ServiceInterruption.Kind.SUSPENSION)
            rows.append({
                "eng": eng,
                "start": seg_start,
                "end": seg_end,
                "designation": eng.position_title,
                "grade": f"SG {eng.salary_grade}, step {seg_step}" if eng.salary_grade else "",
                "status": status_text(eng),
                "salary": salary_text(eng) if last else "",
                "salary_missing": not last,
                "station": eng.office.name,
                "lwop": lwop,
                "suspended": suspended,
                "separated_on": eng.end_date if cause else None,
                "cause": cause,
            })
    return rows


def employment_rows(person):
    """Every appointment and contract for the certificate of employment, oldest first (CR4)."""
    return [{
        "eng": eng,
        "position": eng.position_title,
        "office": eng.office.name,
        "status": status_text(eng),
        "start": eng.start_date,
        "end": eng.end_date if eng.status != Engagement.Status.ACTIVE else None,
        "service": eng.type not in excluded_types(),
    } for eng in person.engagements.select_related("office").order_by("start_date", "pk")]


def coe_context(person, with_pay):
    """Wording of the certificate: present tense while in service, past tense after (CR4)."""
    rows = employment_rows(person)
    current = [r for r in rows if r["end"] is None]
    latest = current[-1] if current else (rows[-1] if rows else None)
    only_contracts = bool(rows) and not any(r["service"] for r in rows)
    pay = ""
    if with_pay and latest:
        eng = latest["eng"]
        if eng.rate is not None:
            basis = {"monthly": "a month", "daily": "a day"}.get(eng.rate_basis, "lump sum")
            pay = f"₱{eng.rate:,.2f} {basis}"
    return {
        "rows": rows,
        "in_service": bool(current),
        "latest": latest,
        "only_contracts": only_contracts,
        "title": "CERTIFICATION OF SERVICE" if only_contracts else "CERTIFICATE OF EMPLOYMENT",
        "honorific": {"M": "Mr.", "F": "Ms."}.get(person.sex, ""),
        "pay": pay,
    }


def has_service_record(person):
    return person.engagements.exclude(type__in=excluded_types()).exists()


def open_count(person):
    return CertificateRequest.objects.filter(person=person, status__in=CertificateRequest.OPEN_STATUSES).count()


def ordinal_day(day):
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"
