"""Compensatory overtime credits (COC) and compensatory time-off (CTO), in hours
(CSC-DBM Joint Circular No. 2, s. 2004; DECISIONS CT1-CT5).

Earned hours are lots that expire; CTO uses the lots that expire first. The ledger is never
edited: a cancelled CTO is marked void and its hours count again."""

from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .models import CocEntry, Engagement, LeaveRequest, LeaveType, Person

HOURS_PER_DAY = Decimal("8")
TWO_PLACES = Decimal("0.01")


def rate(day_kind):
    return Decimal(settings.COC_RESTDAY_RATE if day_kind == CocEntry.DayKind.RESTDAY else settings.COC_WORKDAY_RATE)


def add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def earns_coc(person):
    """JO and COS are not employees and earn no COC (CT1)."""
    return person.engagements.filter(status=Engagement.Status.ACTIVE).exclude(
        type__in=settings.COC_EXCLUDED_TYPES).exists()


def eligible_people():
    active = Engagement.objects.filter(status=Engagement.Status.ACTIVE).exclude(type__in=settings.COC_EXCLUDED_TYPES)
    return Person.objects.filter(pk__in=active.values("person_id"))


def lots(person):
    """Earned lots with the hours left after every CTO, oldest-expiring used first."""
    entries = list(CocEntry.objects.filter(person=person, void=False).order_by("date", "pk"))
    earned = [{"entry": e, "left": e.hours, "expires": e.expires_on} for e in entries if e.kind == CocEntry.Kind.EARNED]
    for use in (e for e in entries if e.kind == CocEntry.Kind.USED):
        need = -use.hours
        for lot in sorted(earned, key=lambda l: (l["expires"], l["entry"].pk)):
            if need <= 0:
                break
            if lot["left"] > 0 and lot["entry"].date <= use.date <= lot["expires"]:
                take = min(lot["left"], need)
                lot["left"] -= take
                need -= take
    return earned


def balance(person, as_of=None):
    as_of = as_of or timezone.localdate()
    return sum((l["left"] for l in lots(person) if l["expires"] >= as_of and l["entry"].date <= as_of), Decimal("0"))


def expiring(person, today, within_days=60):
    """Lots with hours left that expire within the next within_days days."""
    return [l for l in lots(person) if l["left"] > 0 and 0 <= (l["expires"] - today).days <= within_days]


def held_hours(person, exclude=None):
    """Hours of CTO applications still being signed."""
    pending = LeaveRequest.objects.filter(person=person, leave_type=LeaveType.CTO, status=LeaveRequest.Status.PENDING)
    if exclude is not None:
        pending = pending.exclude(pk=exclude.pk)
    return sum(((r.days_with_pay if r.days_with_pay is not None else r.days) * HOURS_PER_DAY for r in pending),
               Decimal("0"))


def available_days(person, on_date=None, exclude=None):
    hours = balance(person, on_date) - held_hours(person, exclude)
    return max(hours, Decimal("0")) / HOURS_PER_DAY


def credit(person, worked_on, hours_worked, day_kind, reference, user, today=None):
    """Credits overtime, within the monthly and total caps (CT2). Returns (entry or None, hours
    credited, hours not credited)."""
    today = today or timezone.localdate()
    computed = (Decimal(hours_worked) * rate(day_kind)).quantize(TWO_PLACES)
    month_total = CocEntry.objects.filter(
        person=person, kind=CocEntry.Kind.EARNED, void=False, date__year=worked_on.year, date__month=worked_on.month,
    ).aggregate(total=Sum("hours"))["total"] or Decimal("0")
    room = min(Decimal(settings.COC_MONTHLY_CAP) - month_total, Decimal(settings.COC_BALANCE_CAP) - balance(person, today))
    credited = max(min(computed, room), Decimal("0")).quantize(TWO_PLACES)
    if credited <= 0:
        return None, Decimal("0"), computed
    entry = CocEntry.objects.create(
        person=person, kind=CocEntry.Kind.EARNED, date=worked_on, hours_worked=hours_worked, day_kind=day_kind,
        hours=credited, expires_on=add_months(worked_on, settings.COC_VALID_MONTHS), reference=reference[:100],
        created_by=user,
        note="" if credited == computed else f"{computed - credited} h over the cap, not credited",
    )
    return entry, credited, computed - credited


def charge(req, user=None):
    """Approved CTO: uses the hours with pay (CT4)."""
    hours = (req.days_with_pay or Decimal("0")) * HOURS_PER_DAY
    if hours > 0:
        CocEntry.objects.create(person=req.person, kind=CocEntry.Kind.USED, date=req.start_date, hours=-hours,
                                leave_request=req, created_by=user, note=f"CTO #{req.pk}")


def restore(req):
    """Cancelled CTO: its hours count again (the entry stays, marked void)."""
    for entry in req.coc_entries.filter(void=False):
        entry.void = True
        entry.note = f"{entry.note} (cancelled)".strip()
        entry.save()
