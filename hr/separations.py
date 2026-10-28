"""Separation from the LGU: the clearance from money, property and work-related accountabilities,
then the appointment is ended with the right status, and the terminal leave is estimated
(DECISIONS SE1-SE5). The signed clearance stays the record (F1); HRMO records each signature.
"""

from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from . import orgchart
from .leave import balance
from .models import ClearanceItem, Engagement, LeaveType, Separation
from .papers import TERMINAL_LEAVE_FACTOR, monthly_rate

Item = ClearanceItem
DONE_STATUSES = [Item.Status.CLEARED, Item.Status.NOT_APPLICABLE]


class SeparationError(Exception):
    """A separation action that is not allowed; the message is shown to the user."""


def start(sep, user):
    """Saves a new separation with one clearance line per configured signatory (SE2)."""
    sep.person = sep.engagement.person
    sep.created_by = user
    sep.save()
    for order, area in enumerate(settings.CLEARANCE_AREAS, start=1):
        Item.objects.create(separation=sep, order=order, area=area[:200])
    return sep


def record_item(item, user, status, signed_by_name="", signed_on=None, remarks=""):
    if not item.separation.is_open:
        raise SeparationError("This separation is closed.")
    if status not in Item.Status.values:
        raise SeparationError("Unknown clearance status.")
    if status == Item.Status.CLEARED and not (signed_by_name and signed_on):
        raise SeparationError("Write who signed the clearance and when.")
    if status == Item.Status.FINDINGS and not remarks:
        raise SeparationError("Write the findings: what is still to be settled.")
    item.status, item.signed_by_name, item.signed_on = status, signed_by_name[:150], signed_on
    item.remarks, item.recorded_by = remarks[:300], user
    item.save()


def outstanding(sep):
    return [i for i in sep.clearance_items.all() if i.status not in DONE_STATUSES]


def complete(sep, user):
    """Ends the appointment once every clearance line is signed or not applicable (SE3). Call inside a
    transaction with `sep` locked; the caller bumps the version and saves it."""
    if not sep.is_open:
        raise SeparationError("This separation is already closed.")
    left = outstanding(sep)
    if left:
        raise SeparationError(f"The clearance is not complete: {', '.join(i.area.split(':')[0] for i in left)}.")
    eng = Engagement.objects.select_for_update().get(pk=sep.engagement_id)
    if eng.status == Engagement.Status.ACTIVE:
        eng.status = Separation.ENDS_AS.get(sep.kind, Engagement.Status.ENDED)
        eng.end_date = sep.last_day
        eng.notes = f"{eng.notes}\n{sep.get_kind_display()}, last day {sep.last_day}.".strip()
        eng.version += 1
        eng.save()
        orgchart.hand_over_direct_reports(eng)
    sep.status, sep.completed_at, sep.completed_by = Separation.Status.COMPLETED, timezone.now(), user


def cancel(sep):
    if not sep.is_open:
        raise SeparationError("This separation is already closed.")
    sep.status = Separation.Status.CANCELLED


def terminal_leave(sep):
    """Leave credits on the last day and the terminal leave benefit, S x D x CF (SE4): S is the
    highest monthly salary on record, D the vacation and sick leave days. An estimate for HRMO."""
    if not sep.person.engagements.filter(type__in=Engagement.LEAVE_CREDIT_TYPES).exists():
        return None
    vl = balance(sep.person, LeaveType.VACATION, as_of=sep.last_day)
    sl = balance(sep.person, LeaveType.SICK, as_of=sep.last_day)
    salaries = [r for r in (monthly_rate(e) for e in sep.person.engagements.all()) if r is not None]
    highest = max(salaries) if salaries else None
    days = max(vl, Decimal("0")) + max(sl, Decimal("0"))
    benefit = (highest * days * TERMINAL_LEAVE_FACTOR).quantize(Decimal("0.01")) if highest is not None else None
    return {"vl": vl, "sl": sl, "days": days, "highest": highest, "factor": TERMINAL_LEAVE_FACTOR,
            "benefit": benefit}
