"""Sample leave applications, vacancies and memos around today for a demo (DEV ONLY, DECISIONS L8, V5, M4).

Adds, once:
- leave applications at each stage of the CS Form No. 6 chain (waiting for the office head, HRMO and
  the Mayor, approved, disapproved), including two for the demo employee if that account exists;
- an open vacancy and one being screened by the HRMPSB, with fake applicants;
- an ICAS-style memo for everyone with a deadline next week (about 40% have acknowledged it) and
  one office memo.
Everything is tagged "[demo board]" (memos: "(sample)" in the number) and added only once.
Runs with auditing off, like the other seeders.
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from hr import leave
from hr.audit import disable_audit
from hr.models import (
    Applicant, Engagement, LeaveApprovalStep, LeaveRequest, LeaveType, Memo, MemoAcknowledgment, Office, Person,
    Vacancy,
)

TAG = "[demo board]"
Step = LeaveApprovalStep


class Command(BaseCommand):
    help = "Adds sample leave applications, vacancies and memos for a demo (DEV ONLY)."

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo_board is for demos only (DJANGO_DEBUG=1).")
        today = timezone.localdate()
        rng = random.Random(42)
        added = []
        with disable_audit(), transaction.atomic():
            if not LeaveRequest.objects.filter(reason__startswith=TAG).exists():
                count = self._leave(today, rng)
                if count:
                    added.append(f"{count} leave applications")
            if not Vacancy.objects.filter(notes__startswith=TAG).exists():
                added.append(f"{self._vacancies(today)} vacancies")
            if not Memo.objects.filter(number__endswith="(sample)").exists():
                added.append(f"{self._memos(today, rng)} memos")
            statuses = self._appointment_statuses()
            if statuses:
                added.append(f"appointment status on {statuses} plantilla items")
        self.stdout.write("Board demo data: " + (", ".join(added) if added else "already added."))

    def _users(self):
        User = get_user_model()
        return {u.username: u for u in User.objects.filter(username__in=["demo_head", "demo_hrmo", "demo_exec",
                                                                        "demo_employee"])}

    def _sign(self, req, kind, user, status=Step.Status.DONE, note=""):
        """Signs a step as `user`; without one, as HRMO recording the paper form (L6)."""
        step = req.steps.get(kind=kind)
        if step.status == Step.Status.SKIPPED:
            return
        step.from_paper = user is None
        step.status, step.acted_by, step.acted_at, step.note = status, user or self.hrmo, timezone.now(), note
        step.save()

    def _leave(self, today, rng):
        users = self._users()
        hrmo, head, mayor = users.get("demo_hrmo"), users.get("demo_head"), users.get("demo_exec")
        self.hrmo = hrmo
        people = list(leave.eligible_people())
        employee = getattr(getattr(users.get("demo_employee"), "profile", None), "person", None)
        if employee in people:
            people.remove(employee)
        rng.shuffle(people)
        # (days from today, length, type, detail, how far it got)
        plans = [
            (9, 3, LeaveType.VACATION, "within_ph", "recommend"),
            (14, 1, LeaveType.SPECIAL_PRIVILEGE, "within_ph", "recommend"),
            (6, 2, LeaveType.VACATION, "abroad", "certify"),
            (-1, 2, LeaveType.SICK, "out_patient", "certify"),
            (4, 5, LeaveType.VACATION, "within_ph", "approve"),
            (2, 1, LeaveType.MANDATORY, "within_ph", "approved"),
            (20, 3, LeaveType.VACATION, "within_ph", "rejected"),
        ]
        targets = list(zip(people, plans))
        if employee and employee.earns_leave_credits():
            targets += [(employee, (12, 2, LeaveType.VACATION, "within_ph", "certify")),
                        (employee, (-20, 1, LeaveType.SICK, "out_patient", "approved"))]
        count = 0
        for person, (offset, length, leave_type, detail, stage) in targets:
            start = today + timedelta(days=offset)
            while start.weekday() >= 5:
                start += timedelta(days=1)
            end = start + timedelta(days=length - 1)
            days = leave.working_days(start, end) or length
            req = LeaveRequest(person=person, leave_type=leave_type, detail=detail,
                               detail_text="Singapore" if detail == "abroad" else ("Fever" if detail == "out_patient" else ""),
                               start_date=start, end_date=end, days=Decimal(days), reason=f"{TAG} Sample application.")
            leave.file_request(req, users.get("demo_employee") if person == employee else None)
            order = ["recommend", "certify", "approve", "approved"]
            reached = order.index(stage) if stage in order else 1
            if reached >= 1 or stage == "rejected":
                self._sign(req, Step.Kind.RECOMMEND, head if person == employee else None)
            if stage == "rejected":
                self._sign(req, Step.Kind.CERTIFY, hrmo)
                req.days_with_pay, req.days_without_pay = leave.pay_split(req)
                self._sign(req, Step.Kind.APPROVE, mayor, Step.Status.DISAPPROVED, "Exigency of the service.")
                req.status, req.decision_note, req.decided_at = (LeaveRequest.Status.REJECTED,
                                                                 "Exigency of the service.", timezone.now())
            if reached >= 2:
                req.days_with_pay, req.days_without_pay = leave.pay_split(req)
                req.certified_balances = {"as_of": today.isoformat(),
                                          "vl": str(leave.balance(person, LeaveType.VACATION)),
                                          "sl": str(leave.balance(person, LeaveType.SICK))}
                self._sign(req, Step.Kind.CERTIFY, hrmo)
            if reached >= 3:
                self._sign(req, Step.Kind.APPROVE, mayor)
                leave._approve(req, mayor, timezone.now())
            req.save()
            count += 1
        return count

    def _appointment_statuses(self):
        """Fills the Plantilla's empty Status column with Permanent, so the demo report has no gaps (RP1)."""
        count = 0
        for eng in Engagement.objects.filter(type=Engagement.Type.PLANTILLA, appointment_status=""):
            eng.appointment_status = Engagement.AppointmentStatus.PERMANENT
            eng.save()
            count += 1
        return count

    def _vacancies(self, today):
        mswdo = (Office.objects.filter(name__icontains="Social Welfare").first() or Office.objects.first())
        mto = Office.objects.filter(acronym="MTO").first() or Office.objects.first()
        Vacancy.objects.create(
            office=mswdo, position_title="Social Welfare Aide", item_number="SP-MSWDO-019", salary_grade=4,
            monthly_salary=Decimal("15150.00"),
            education="High School Graduate (prior to 2016) OR Completion of Grade 10/Junior High School (starting 2016)",
            training="None required", experience="None required",
            eligibility="None required (CSC MC 11, s. 1996, as amended by CSC MC 10, s. 2013 - Cat. III)",
            competencies="Core: Technological literacy; Records management; Written communication. "
                         "Organizational: Integrity and ethics; Customer focus; Communication.",
            status=Vacancy.Status.OPEN, published_on=today - timedelta(days=5), deadline=today + timedelta(days=12),
            csc_received_on=today - timedelta(days=6),
            posted_at="HRMO Bulletin Board, 2nd Floor Old Municipal Building; Frontline Services, Ground Floor "
                      "Executive and Legislative Building",
            notes=f"{TAG} Sample vacancy.",
        )
        screening = Vacancy.objects.create(
            office=mto, position_title="Administrative Aide IV (Clerk II)", item_number="SP-MTO-007", salary_grade=4,
            monthly_salary=Decimal("15150.00"), education="Completion of two years of studies in college",
            training="None required", experience="None required", eligibility="Career Service (Subprofessional) / "
            "First Level Eligibility", status=Vacancy.Status.SCREENING, published_on=today - timedelta(days=30),
            deadline=today - timedelta(days=10), csc_received_on=today - timedelta(days=31),
            board_met_on=today + timedelta(days=3), board_venue="1st floor Municipal Tourism Building",
            posted_at="HRMO Bulletin Board, 2nd Floor Old Municipal Building", notes=f"{TAG} Sample vacancy.",
        )
        for i, (last, first, ok) in enumerate([("Dela Paz", "Rowena", True), ("Magbanua", "Jerome", True),
                                               ("Sarmiento", "Liza", False)]):
            Applicant.objects.create(
                vacancy=screening, last_name=last, first_name=first, signified_on=today - timedelta(days=25 - i * 4),
                has_pds=True, has_rating=i != 2, has_eligibility=ok, has_tor=True, meets_qs=ok,
                rank=i + 1 if ok else None, remarks="" if ok else "No eligibility",
            )
        return 2

    def _memos(self, today, rng):
        hrmo = self._users().get("demo_hrmo")
        everyone = Memo.objects.create(
            number="HRMO Memorandum No. 010, series of 2026 (sample)", dated=today - timedelta(days=3),
            subject="Compliance with the Integrated Competency Assessment System (ICAS)",
            addressed_to="All appointive employees and JO/COS personnel, Local Government Unit of San Pascual",
            body=("As part of the Learning and Development planning process, all appointive employees including "
                  "JO/COS personnel are requested to accomplish the ICAS:\n\n"
                  "Step 1: Accomplish the self-assessment portion of the ICAS form.\n"
                  "Step 2: Your immediate supervisor reviews, comments and approves the assessment.\n"
                  "Step 3: HRMO submits the supervisor-endorsed forms to the CSC Regional Office.\n\n"
                  "HRMO will give separate instructions in the official chat group."),
            signed_by="Municipal Mayor", released_on=today - timedelta(days=3), comply_by=today + timedelta(days=5),
            created_by=hrmo,
        )
        active = list(Person.objects.filter(engagements__status=Engagement.Status.ACTIVE).distinct())
        for person in rng.sample(active, k=int(len(active) * 0.4)):
            MemoAcknowledgment.objects.create(memo=everyone, person=person, from_paper=rng.random() < 0.3)
        mto = Office.objects.filter(acronym="MTO").first()
        if mto:
            Memo.objects.create(
                number="MTO Office Order No. 3, series of 2026 (sample)", dated=today - timedelta(days=1),
                subject="Collection window schedule during the town fiesta", addressed_to="All MTO personnel",
                audience=Memo.Audience.OFFICE, office=mto, signed_by="Municipal Treasurer",
                body="The collection windows open at 7:00 AM on fiesta week. Please coordinate your leave with your "
                     "section chief.", created_by=hrmo,
            )
            return 2
        return 1
