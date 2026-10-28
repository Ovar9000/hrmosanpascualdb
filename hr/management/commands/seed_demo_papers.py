"""Sample data for the papers, pass slips, separations, awards and development plans (DEV ONLY,
DECISIONS NS4, PS5, SE5, AW3, ID3).

Adds, once:
- two sample salary schedules (last January and next January) with made-up amounts, marked
  "(sample, not the SSL table)", so NOSAs and NOSIs print with figures;
- appointment paper details (nature, signing, oath and assumption dates) on recent plantilla
  appointments, so the ROAI has rows;
- today's pass slips at each stage (waiting for HRMO, waiting for the Mayor, out now, back);
- a separation with half of its clearance signed;
- a few awards, and a draft development plan for the demo employee.
Runs with auditing off, like the other seeders.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from hr import pass_slips, separations
from hr.audit import disable_audit
from hr.models import (
    Award, DevelopmentItem, DevelopmentPlan, Engagement, PassSlip, PassSlipStep, SalaryRate, SalarySchedule,
    Separation, UserProfile,
)

SAMPLE = "(sample, not the SSL table)"


def sample_amount(grade, step, year):
    """Made-up, rising amounts: about ₱13,000 at SG 1 to ₱190,000 at SG 33, steps 1.5% apart."""
    base = Decimal(13000) * (Decimal("1.093") ** (grade - 1)) * (Decimal("1.04") ** (year - 2026))
    return (base * (Decimal("1.015") ** (step - 1))).quantize(Decimal("1"))


class Command(BaseCommand):
    help = "Adds sample salary schedules, papers, pass slips, a separation, awards and a plan (DEV ONLY)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_papers is for demos only (DJANGO_DEBUG=1).")
        today = timezone.localdate()
        users = {u.username: u for u in get_user_model().objects.filter(username__startswith="demo_")}
        added = []
        with disable_audit(), transaction.atomic():
            if not SalarySchedule.objects.filter(name__endswith=SAMPLE).exists():
                for year in (today.year, today.year + 1):
                    schedule = SalarySchedule.objects.create(name=f"Salary schedule {year} {SAMPLE}",
                                                             effective_date=date(year, 1, 1),
                                                             basis="Sample only: paste the LGU's schedule")
                    SalaryRate.objects.bulk_create(SalaryRate(schedule=schedule, salary_grade=g, step=s,
                                                              monthly=sample_amount(g, s, year))
                                                   for g in range(1, 34) for s in range(1, 9))
                added.append("2 sample salary schedules")
            papers = self._papers(today)
            if papers:
                added.append(f"paper details on {papers} appointments")
            if not PassSlip.objects.filter(purpose__startswith="[demo]").exists():
                added.append(f"{self._slips(today, users)} pass slips")
            if not Separation.objects.filter(reason__startswith="[demo]").exists() and self._separation(users):
                added.append("1 separation")
            if not Award.objects.filter(notes__startswith="[demo]").exists():
                added.append(f"{self._awards(today)} awards")
            if self._plan(today, users):
                added.append("1 development plan")
        self.stdout.write("Papers demo data: " + (", ".join(added) if added else "already added."))

    def _papers(self, today):
        """The 12 latest plantilla and casual appointments, once: only while no appointment has a signing date."""
        if Engagement.objects.filter(issued_on__isnull=False).exists():
            return 0
        recent = (Engagement.objects.filter(type__in=[Engagement.Type.PLANTILLA, Engagement.Type.CASUAL])
                  .order_by("-start_date"))
        count = 0
        for eng in recent[:12]:
            eng.nature = Engagement.Nature.ORIGINAL if eng.type == Engagement.Type.PLANTILLA else Engagement.Nature.RENEWAL
            eng.issued_on = eng.oath_on = eng.assumed_on = eng.start_date
            if eng.type == Engagement.Type.PLANTILLA:
                eng.vice_reason = "new item"
            eng.save()
            count += 1
        return count

    def _filer(self, person, users):
        profile = UserProfile.objects.filter(person=person).select_related("user").first()
        return profile.user if profile else users.get("demo_hrmo")

    def _slips(self, today, users):
        people = list(Engagement.objects.filter(status=Engagement.Status.ACTIVE, type=Engagement.Type.PLANTILLA)
                      .exclude(person__user_profile__user__username__in=["demo_hrmo", "demo_exec"])
                      .select_related("person").order_by("person__last_name")[:4])
        employee = users.get("demo_employee")
        if employee and getattr(employee, "profile", None) and employee.profile.person_id:
            own = Engagement.objects.filter(person_id=employee.profile.person_id, status=Engagement.Status.ACTIVE).first()
            if own:
                people = [own] + [e for e in people if e.person_id != own.person_id][:3]
        hrmo, mayor = users.get("demo_hrmo"), users.get("demo_exec")
        plans = [
            ("official", time(9, 0), time(11, 0), "Municipal Treasurer's Office", None),
            ("personal", time(10, 0), time(11, 0), "Rural health unit", "note"),
            ("official", time(13, 0), time(15, 0), "Provincial Capitol, Masbate City", "out"),
            ("personal", time(8, 30), time(9, 30), "Bank", "back"),
        ]
        count = 0
        for eng, (kind, out, back, where, stage) in zip(people, plans):
            slip = PassSlip(person=eng.person, kind=kind, slip_date=today, time_out=out, expected_back=back,
                            destination=where, purpose="[demo] " + ("Deliver documents" if kind == "official" else "Personal errand"))
            pass_slips.file_slip(slip, self._filer(eng.person, users))
            now = timezone.now()
            for step in slip.steps.all():
                if stage is None or (stage == "note" and step.kind != PassSlipStep.Kind.NOTE):
                    break
                signer = hrmo if step.kind == PassSlipStep.Kind.NOTE else mayor
                step.status, step.acted_by, step.acted_at = PassSlipStep.Status.DONE, signer, now
                step.save()
            if stage in ("out", "back"):
                slip.status, slip.decided_at, slip.actual_out = PassSlip.Status.APPROVED, now, out
                if stage == "back":
                    slip.actual_back = (datetime.combine(today, back) + timedelta(minutes=10)).time()
                slip.save()
            count += 1
        return count

    def _separation(self, users):
        eng = (Engagement.objects.filter(status=Engagement.Status.ACTIVE, type=Engagement.Type.PLANTILLA,
                                         separations__isnull=True)
               .exclude(person__user_profile__isnull=False).order_by("person__birthdate").first())
        if not eng:
            return False
        sep = Separation(engagement=eng, kind=Separation.Kind.RETIREMENT_COMPULSORY,
                         last_day=timezone.localdate() + timedelta(days=45), letter_received_on=timezone.localdate(),
                         reason="[demo] Turns 65 next month.")
        separations.start(sep, users.get("demo_hrmo"))
        for item in sep.clearance_items.all()[:2]:
            item.status, item.signed_by_name, item.signed_on = "cleared", "Sample signatory", timezone.localdate()
            item.save()
        return True

    def _awards(self, today):
        engs = list(Engagement.objects.filter(status=Engagement.Status.ACTIVE).select_related("person")
                    .order_by("start_date")[:3])
        titles = [("loyalty", "20-year loyalty award"), ("performance", f"Outstanding Employee of the Year {today.year - 1}"),
                  ("special", "Special citation: Typhoon response")]
        for eng, (category, title) in zip(engs, titles):
            Award.objects.create(person=eng.person, category=category, title=title,
                                 given_on=date(today.year, 1, 15), given_by="Municipal Mayor",
                                 notes="[demo] Sample award.")
        return len(engs)

    def _plan(self, today, users):
        employee = users.get("demo_employee")
        person_id = getattr(getattr(employee, "profile", None), "person_id", None)
        if not person_id or DevelopmentPlan.objects.filter(person_id=person_id, year=today.year).exists():
            return False
        plan = DevelopmentPlan.objects.create(person_id=person_id, year=today.year,
                                              career_goal="Qualify for the next higher position in the office.")
        DevelopmentItem.objects.create(plan=plan, competency="Records management", objective="Keep the 201 files in order",
                                       activity="CSC records management course", target_date=date(today.year, 11, 30))
        DevelopmentItem.objects.create(plan=plan, competency="Spreadsheets", activity="Coaching by the office head",
                                       support="Two hours a week")
        return True
