"""Appointment papers and the ROAI, JO/COS papers, salary schedules with NOSA and NOSI, pass slips,
separation and clearance, awards, development plans and the workforce profile."""

from datetime import date, time, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from hr import papers, pass_slips, separations
from hr.models import (
    Award, ClearanceItem, DevelopmentPlan, Engagement, LeaveCredit, LeaveType, PassSlip, Person, SalaryAction,
    SalaryRate, SalarySchedule, Separation, Vacancy,
)
from hr.tests import Step4CSecurityAndRolesTestCase

ALL_MODULES = ["pass_slips", "development_plans", "awards", "workforce"]


def _table(base=10000, grades=3):
    """A pasted schedule: one line per grade, 8 steps, rising."""
    return "\n".join(" ".join(f"{base + g * 1000 + s * 100:,}.00" for s in range(8)) for g in range(grades))


class LguBase(TestCase):
    setUp_roles = Step4CSecurityAndRolesTestCase.setUp

    def setUp(self):
        self.setUp_roles()
        self.jo_person = Person.objects.create(employee_no="SP-00404", last_name="Silang", first_name="Gabriela",
                                               sex="F", birthdate=date(1995, 3, 19))
        self.jo = Engagement.objects.create(
            person=self.jo_person, office=self.office_a, type=Engagement.Type.JOB_ORDER, position_title="Utility Worker",
            rate=Decimal("450.00"), rate_basis=Engagement.RateBasis.DAILY, start_date=date(2026, 7, 1),
            end_date=date(2026, 12, 31), duties="Cleans the session hall and the Mayor's office.",
        )

    def get(self, user, url, **params):
        self.client.force_login(user)
        return self.client.get(url, params)

    def post(self, user, url, data):
        self.client.force_login(user)
        return self.client.post(url, data)


class WordsAndScheduleTestCase(TestCase):
    def test_amount_in_words(self):
        self.assertEqual(papers.amount_in_words(Decimal("25439.50")),
                         "Twenty-Five Thousand Four Hundred Thirty-Nine Pesos and 50/100")
        self.assertEqual(papers.amount_in_words(Decimal("1")), "One Peso Only")
        self.assertEqual(papers.number_in_words(1_000_000), "One Million")
        self.assertEqual(papers.amount_in_words(None), "")

    def test_parse_schedule_by_order_or_with_grade(self):
        header = "Salary grade " + " ".join(f"Step {s}" for s in range(1, 9))
        cells = papers.parse_schedule(header + "\n" + _table(grades=2))
        self.assertEqual(len(cells), 16)
        self.assertEqual(cells[(1, 1)], Decimal("10000.00"))
        self.assertEqual(cells[(2, 8)], Decimal("11700.00"))
        with_grade = papers.parse_schedule("15 " + " ".join(str(30000 + s) for s in range(8)))
        self.assertEqual(with_grade[(15, 3)], Decimal("30002"))

    def test_parse_schedule_refuses_bad_lines(self):
        for text, message in (("1 20000 21000 22000", "expected 8"), ("", "Paste the table"),
                              ("Step 1 2 3", "Paste the table"),
                              ("5 " + "15000 " * 8 + "\n5 " + "16000 " * 8, "appears twice")):
            with self.subTest(text=text), self.assertRaisesMessage(papers.ScheduleError, message):
                papers.parse_schedule(text)

    def test_warnings_for_amounts_that_go_down(self):
        cells = papers.parse_schedule("10000 20000 15000 40000 50000 60000 70000 80000")
        self.assertEqual(papers.schedule_warnings(cells), ["SG 1: step 3 is less than step 2."])


class AppointmentPapersTestCase(LguBase):
    def test_hrmo_fills_in_and_prints_cs_form_33(self):
        url = f"/engagements/{self.eng_a.pk}/papers/"
        resp = self.post(self.hrmo_user, url, {
            "version": self.eng_a.version, "nature": "promotion", "vice_name": "Juan Luna", "vice_reason": "retired",
            "issued_on": "2025-01-01", "oath_on": "2025-01-02", "assumed_on": "2025-01-02", "duties": "",
        })
        self.assertRedirects(resp, url)
        self.eng_a.refresh_from_db()
        self.assertEqual((self.eng_a.nature, self.eng_a.vice_name, self.eng_a.version), ("promotion", "Juan Luna", 2))
        page = self.get(self.hrmo_user, f"{url}appointment/")
        self.assertContains(page, "Fifty Thousand Pesos Only")
        self.assertContains(page, "JOSE")
        self.assertContains(page, "Promotion")
        self.assertContains(self.get(self.hrmo_user, f"{url}oath/"), "SO HELP ME GOD")
        self.assertContains(self.get(self.hrmo_user, f"{url}assumption/"), "January 02, 2025")

    def test_stale_version_is_refused(self):
        resp = self.post(self.hrmo_user, f"/engagements/{self.eng_a.pk}/papers/",
                         {"version": self.eng_a.version + 5, "nature": "original"})
        self.assertContains(resp, "Someone changed this appointment")

    def test_assumption_before_signing_is_refused(self):
        resp = self.post(self.hrmo_user, f"/engagements/{self.eng_a.pk}/papers/", {
            "version": self.eng_a.version, "issued_on": "2025-02-01", "assumed_on": "2025-01-15"})
        self.assertContains(resp, "on or after the date the appointment was signed")

    def test_who_sees_the_papers(self):
        url = f"/engagements/{self.eng_a.pk}/papers/"
        self.assertEqual(self.get(self.auditor_user, url).status_code, 200)
        self.assertEqual(self.post(self.auditor_user, url, {"version": 1}).status_code, 403)
        for user in (self.head_a, self.staff_a, self.emp_a_user, self.mayor, self.sysadmin):
            with self.subTest(user=user.username):
                self.assertEqual(self.get(user, url).status_code, 403)

    def test_hrmo_never_fills_in_their_own(self):
        from hr.models import UserProfile

        UserProfile.objects.create(user=self.hrmo_user, person=self.jo_person)
        resp = self.post(self.hrmo_user, f"/engagements/{self.jo.pk}/papers/", {"version": 1, "nature": "original"})
        self.assertEqual(resp.status_code, 403)

    def test_papers_fit_the_kind_of_engagement(self):
        self.assertEqual(self.get(self.hrmo_user, f"/engagements/{self.eng_a.pk}/papers/contract/").status_code, 403)
        self.assertEqual(self.get(self.hrmo_user, f"/engagements/{self.jo.pk}/papers/appointment/").status_code, 403)
        page = self.get(self.hrmo_user, f"/engagements/{self.jo.pk}/papers/contract/")
        self.assertContains(page, "JOB ORDER")
        self.assertContains(page, "Four Hundred Fifty Pesos Only")
        self.assertContains(page, "not considered government service")

    def test_publication_comes_from_the_vacancy_for_the_item(self):
        Vacancy.objects.create(office=self.office_a, position_title="Executive Assistant IV", item_number="SP-MO-001",
                               salary_grade=22, monthly_salary=Decimal("50000"), education="x", eligibility="x",
                               status=Vacancy.Status.FILLED, published_on=date(2024, 11, 1), deadline=date(2024, 11, 15),
                               posted_at="HRMO board")
        self.assertEqual(papers.publication_for(self.eng_a).posted_at, "HRMO board")
        self.assertContains(self.get(self.hrmo_user, f"/engagements/{self.eng_a.pk}/papers/appointment/"), "HRMO board")


class RoaiAndContractsTestCase(LguBase):
    def test_roai_uses_signing_date_else_start_date(self):
        self.eng_b.issued_on = date(2025, 2, 10)
        self.eng_b.save()
        jan = papers.roai_rows(2025, 1)
        self.assertEqual([(r["eng"], r["issued_guess"]) for r in jan], [(self.eng_a, True)])
        self.assertEqual([r["eng"] for r in papers.roai_rows(2025, 2)], [self.eng_b])
        resp = self.get(self.hrmo_user, "/reports/roai/", month="2025-01")
        self.assertContains(resp, "no signing date recorded")
        self.assertEqual(self.get(self.hrmo_user, "/reports/roai/", month="2025-01", export="xlsx")["Content-Type"],
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertContains(self.get(self.hrmo_user, "/reports/roai/", month="2025-01", print="1"), "CS Form No. 2")

    def test_job_orders_are_not_on_the_roai(self):
        self.assertNotIn(self.jo, [r["eng"] for r in papers.roai_rows(2026, 7)])

    def test_batch_contracts_print_one_per_page(self):
        other = Engagement.objects.create(
            person=self.person_b, office=self.office_b, type=Engagement.Type.CONTRACT_OF_SERVICE,
            position_title="IT Consultant", start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))
        resp = self.get(self.hrmo_user, "/papers/contracts/print/", id=[self.jo.pk, other.pk, self.eng_a.pk])
        self.assertContains(resp, "JOB ORDER")
        self.assertContains(resp, "CONTRACT OF SERVICE")
        self.assertNotContains(resp, "Executive Assistant IV")  # plantilla is not a contract
        self.assertContains(self.get(self.hrmo_user, "/papers/contracts/"), "Silang, Gabriela")

    def test_jo_cos_list_by_semester(self):
        first = papers.jo_cos_rows(*papers.semester_bounds(2026, 1))
        second = papers.jo_cos_rows(*papers.semester_bounds(2026, 2))
        self.assertNotIn(self.jo, first)
        self.assertIn(self.jo, second)
        resp = self.get(self.hrmo_user, "/reports/jo-cos/", year="2026", half="2")
        self.assertContains(resp, "Silang, Gabriela")
        self.assertContains(resp, "General Fund")
        self.assertEqual(self.get(self.head_a, "/reports/jo-cos/").status_code, 403)


class SalaryScheduleTestCase(LguBase):
    def _schedule(self, day, base):
        self.client.force_login(self.hrmo_user)
        lines = "\n".join(f"{g} " + " ".join(str(base + g * 1000 + s * 100) for s in range(8)) for g in (6, 22))
        return self.client.post("/salary-schedules/", {"name": f"Tranche {day}", "effective_date": day, "table": lines})

    def test_paste_schedule_and_nosa(self):
        self._schedule("2025-01-01", 10000)
        resp = self._schedule("2026-01-01", 11000)
        new = SalarySchedule.objects.get(effective_date=date(2026, 1, 1))
        self.assertRedirects(resp, f"/salary-schedules/{new.pk}/")
        self.assertEqual(new.rates.count(), 16)
        rows, previous = papers.nosa_rows(new)
        row = next(r for r in rows if r["eng"] == self.eng_a)
        self.assertEqual(previous.effective_date, date(2025, 1, 1))
        self.assertEqual((row["old"], row["new"], row["diff"]), (Decimal("32000"), Decimal("33000"), Decimal("1000")))
        self.assertContains(self.get(self.hrmo_user, f"/salary-schedules/{new.pk}/", print="nosa"),
                            "NOTICE OF SALARY ADJUSTMENT")

    def test_same_effective_date_twice_is_refused(self):
        self._schedule("2026-01-01", 10000)
        self.assertContains(self._schedule("2026-01-01", 10000), "already takes effect on this date")

    def test_apply_updates_stored_rates(self):
        self._schedule("2026-01-01", 11000)
        schedule = SalarySchedule.objects.get()
        resp = self.post(self.hrmo_user, f"/salary-schedules/{schedule.pk}/", {"action": "apply"})
        self.assertRedirects(resp, f"/salary-schedules/{schedule.pk}/")
        self.eng_a.refresh_from_db()
        self.assertEqual(self.eng_a.rate, Decimal("33000.00"))
        self.assertIn("Tranche 2026-01-01", self.eng_a.notes)
        self.assertEqual(self.post(self.auditor_user, f"/salary-schedules/{schedule.pk}/", {"action": "apply"}).status_code, 403)

    def test_future_schedule_cannot_be_applied(self):
        future = (timezone.localdate() + timedelta(days=30)).isoformat()
        self._schedule(future, 11000)
        schedule = SalarySchedule.objects.get()
        self.post(self.hrmo_user, f"/salary-schedules/{schedule.pk}/", {"action": "apply"})
        self.eng_a.refresh_from_db()
        self.assertEqual(self.eng_a.rate, Decimal("50000.00"))

    def test_nosi_amounts_from_the_schedule(self):
        schedule = SalarySchedule.objects.create(name="S", effective_date=date(2025, 1, 1))
        for s in range(1, 9):
            SalaryRate.objects.create(schedule=schedule, salary_grade=22, step=s, monthly=Decimal(60000 + s * 500))
        case = SalaryAction.objects.create(kind=SalaryAction.Kind.STEP_INCREMENT, person=self.person_a, engagement=self.eng_a,
                                           milestone="step:x:2", status=SalaryAction.Status.SIGNATURE,
                                           service_from=date(2025, 1, 1), due_date=date(2028, 1, 1), from_step=1, to_step=2)
        context = papers.nosi_context(case)
        self.assertEqual((context["old"], context["new"], context["diff"]), (Decimal("60500"), Decimal("61000"), Decimal("500")))
        self.assertContains(self.get(self.hrmo_user, f"/salary-actions/{case.pk}/nosi/"), "NOTICE OF STEP INCREMENT")
        case.status = SalaryAction.Status.DUE
        case.save()
        self.assertEqual(self.get(self.hrmo_user, f"/salary-actions/{case.pk}/nosi/").status_code, 403)


@override_settings(HR_MODULES=ALL_MODULES)
class PassSlipTestCase(LguBase):
    def _file(self, **extra):
        data = {"kind": "personal", "slip_date": timezone.localdate().isoformat(), "time_out": "09:00",
                "expected_back": "10:30", "destination": "Bank", "purpose": "Pay a bill"}
        data.update(extra)
        return self.post(self.emp_a_user, "/pass-slips/new/", data)

    def _act(self, user, slip, action, **extra):
        slip.refresh_from_db()
        return self.post(user, f"/pass-slips/{slip.pk}/", {"action": action, "version": slip.version, **extra})

    def test_employee_hrmo_mayor_chain(self):
        self._file()
        slip = PassSlip.objects.get()
        self.assertEqual([s.kind for s in slip.steps.all()], ["note", "approve"])
        self.assertEqual(slip.office, self.office_a)
        self.assertEqual(pass_slips.inbox(self.hrmo_user), [slip])
        self.assertEqual(pass_slips.inbox(self.mayor), [])
        self._act(self.mayor, slip, "done")  # not the Mayor's turn yet
        slip.refresh_from_db()
        self.assertEqual(slip.steps.get(kind="note").status, "pending")
        self._act(self.hrmo_user, slip, "done")
        self.assertEqual(pass_slips.inbox(self.mayor), [slip])
        self._act(self.mayor, slip, "done")
        slip.refresh_from_db()
        self.assertEqual(slip.status, PassSlip.Status.APPROVED)

    def test_nobody_signs_their_own(self):
        self._file()
        slip = PassSlip.objects.get()
        self.assertEqual(pass_slips.can_act(self.emp_a_user, slip.current_step), (False, False))

    def test_disapproval_needs_a_reason(self):
        self._file()
        slip = PassSlip.objects.get()
        self._act(self.hrmo_user, slip, "disapproved")
        slip.refresh_from_db()
        self.assertEqual(slip.status, "pending")
        self._act(self.hrmo_user, slip, "disapproved", note="Office is short-staffed today")
        slip.refresh_from_db()
        self.assertEqual(slip.status, "disapproved")

    def test_employee_cannot_file_for_yesterday(self):
        resp = self._file(slip_date=(timezone.localdate() - timedelta(days=1)).isoformat())
        self.assertContains(resp, "before you go out")
        self.assertFalse(PassSlip.objects.exists())

    def test_times_and_monthly_personal_minutes(self):
        self._file()
        slip = PassSlip.objects.get()
        self._act(self.hrmo_user, slip, "done")
        self._act(self.mayor, slip, "done")
        self._act(self.emp_a_user, slip, "out")
        slip.refresh_from_db()
        self.assertIsNotNone(slip.actual_out)
        self._act(self.hrmo_user, slip, "times", out="09:05", back="10:35")
        slip.refresh_from_db()
        self.assertEqual((slip.actual_out, slip.actual_back, slip.minutes_out), (time(9, 5), time(10, 35), 90))
        today = timezone.localdate()
        _slips, totals = pass_slips.month_log(today.year, today.month)
        self.assertEqual((totals[0]["person"], totals[0]["personal"], totals[0]["personal_minutes"]), (self.person_a, 1, 90))
        self.assertEqual(self.get(self.hrmo_user, "/pass-slips/log/").status_code, 200)
        self.assertEqual(self.get(self.head_a, "/pass-slips/log/").status_code, 403)

    def test_employee_cannot_correct_times(self):
        self._file()
        slip = PassSlip.objects.get()
        self._act(self.hrmo_user, slip, "done")
        self._act(self.mayor, slip, "done")
        self._act(self.emp_a_user, slip, "times", out="07:00")
        slip.refresh_from_db()
        self.assertIsNone(slip.actual_out)

    @override_settings(PASS_SLIP_STEPS=["recommend", "approve"], HR_MODULES=ALL_MODULES)
    def test_office_head_recommends_when_configured(self):
        self._file()
        slip = PassSlip.objects.get()
        self.assertEqual(pass_slips.inbox(self.head_a), [slip])
        self.assertEqual(self.get(self.staff_a, f"/pass-slips/{slip.pk}/").status_code, 403)

    def test_cancel_before_going_out(self):
        self._file()
        slip = PassSlip.objects.get()
        self._act(self.emp_a_user, slip, "cancel")
        slip.refresh_from_db()
        self.assertEqual(slip.status, "cancelled")
        self.assertContains(self.get(self.emp_a_user, f"/pass-slips/{slip.pk}/print/"), "PASS SLIP")


class SeparationTestCase(LguBase):
    def _start(self, **extra):
        data = {"engagement": self.eng_a.pk, "kind": "resignation", "last_day": "2026-11-30",
                "letter_received_on": "2026-10-28", "reason": ""}
        data.update(extra)
        return self.post(self.hrmo_user, f"/people/{self.person_a.pk}/separation/new/", data)

    def _post(self, sep, **data):
        sep.refresh_from_db()
        return self.post(self.hrmo_user, f"/separations/{sep.pk}/", {"version": sep.version, **data})

    def test_clearance_then_completion_ends_the_appointment(self):
        self._start()
        sep = Separation.objects.get()
        items = list(sep.clearance_items.all())
        self.assertEqual(len(items), 5)
        self._post(sep, action="complete")
        self.eng_a.refresh_from_db()
        self.assertEqual(self.eng_a.status, Engagement.Status.ACTIVE)  # clearance not complete
        self._post(sep, action="item", item=items[0].pk, status="cleared")  # no signer: refused
        self.assertEqual(ClearanceItem.objects.get(pk=items[0].pk).status, "pending")
        for item in items[:-1]:
            self._post(sep, action="item", item=item.pk, status="cleared", signed_by_name="A. Signer", signed_on="2026-11-20")
        self._post(sep, action="item", item=items[-1].pk, status="na")
        self._post(sep, action="complete")
        sep.refresh_from_db()
        self.eng_a.refresh_from_db()
        self.assertEqual(sep.status, Separation.Status.COMPLETED)
        self.assertEqual((self.eng_a.status, self.eng_a.end_date), (Engagement.Status.RESIGNED, date(2026, 11, 30)))
        self.assertContains(self.get(self.hrmo_user, f"/separations/{sep.pk}/print/"), "A. Signer")

    def test_findings_need_remarks(self):
        self._start()
        sep = Separation.objects.get()
        item = sep.clearance_items.first()
        self._post(sep, action="item", item=item.pk, status="findings")
        self.assertEqual(ClearanceItem.objects.get(pk=item.pk).status, "pending")
        self._post(sep, action="item", item=item.pk, status="findings", remarks="Unliquidated cash advance")
        self.assertEqual(ClearanceItem.objects.get(pk=item.pk).status, "findings")

    def test_one_open_separation_per_appointment(self):
        self._start()
        self.assertContains(self._start(), "already in progress")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Separation.objects.create(person=self.person_a, engagement=self.eng_a, kind="resignation",
                                      last_day=date(2026, 12, 1))

    def test_terminal_leave_estimate(self):
        LeaveCredit.objects.create(person=self.person_a, leave_type=LeaveType.VACATION, days=Decimal("100"), as_of=date(2026, 1, 1))
        LeaveCredit.objects.create(person=self.person_a, leave_type=LeaveType.SICK, days=Decimal("50"), as_of=date(2026, 1, 1))
        self._start()
        estimate = separations.terminal_leave(Separation.objects.get())
        self.assertEqual(estimate["days"], Decimal("150"))
        self.assertEqual(estimate["benefit"], (Decimal("50000") * 150 * papers.TERMINAL_LEAVE_FACTOR).quantize(Decimal("0.01")))

    def test_who_can_do_what(self):
        self._start()
        sep = Separation.objects.get()
        self.assertEqual(self.get(self.auditor_user, f"/separations/{sep.pk}/").status_code, 200)
        self.assertEqual(self.post(self.auditor_user, f"/separations/{sep.pk}/", {"action": "cancel"}).status_code, 403)
        for user in (self.head_a, self.emp_a_user, self.mayor):
            with self.subTest(user=user.username):
                self.assertEqual(self.get(user, "/separations/").status_code, 403)

    def test_retirement_ends_as_retired(self):
        self._start(kind="retire_compulsory")
        sep = Separation.objects.get()
        sep.clearance_items.update(status="na")
        self._post(sep, action="complete")
        self.eng_a.refresh_from_db()
        self.assertEqual(self.eng_a.status, Engagement.Status.RETIRED)


@override_settings(HR_MODULES=ALL_MODULES)
class AwardsPlansWorkforceTestCase(LguBase):
    def test_hrmo_records_an_award_for_many(self):
        resp = self.post(self.hrmo_user, "/awards/new/", {
            "category": "performance", "title": "Outstanding Employee 2026", "given_on": "2026-10-01",
            "given_by": "PRAISE Committee", "person": [self.person_a.pk, self.person_b.pk]})
        self.assertRedirects(resp, "/awards/?year=2026")
        self.assertEqual(Award.objects.count(), 2)
        self.assertContains(self.get(self.emp_a_user, "/awards/", year="2026"), "Outstanding Employee 2026")
        self.assertEqual(self.post(self.emp_a_user, "/awards/new/", {}).status_code, 403)
        own = Award.objects.get(person=self.person_a)
        self.assertContains(self.get(self.emp_a_user, f"/awards/{own.pk}/print/"), "Certificate of Recognition")
        other = Award.objects.get(person=self.person_b)
        self.assertEqual(self.get(self.emp_a_user, f"/awards/{other.pk}/print/").status_code, 403)

    def test_loyalty_case_fills_in_the_award(self):
        case = SalaryAction.objects.create(kind=SalaryAction.Kind.LOYALTY, person=self.person_b, engagement=self.eng_b,
                                           milestone="loyalty:10", status=SalaryAction.Status.ISSUED, years=10,
                                           service_from=date(2015, 1, 1), due_date=date(2025, 1, 1),
                                           signed_on=date(2025, 1, 5), nosi_number="LA-2025-01")
        resp = self.get(self.hrmo_user, "/awards/new/", salary_action=case.pk)
        self.assertContains(resp, "10-year loyalty award")
        self.assertContains(resp, "LA-2025-01")

    def test_development_plan_written_sent_and_reviewed(self):
        url = f"/people/{self.person_a.pk}/development-plan/"
        self.post(self.emp_a_user, url, {"year": 2026, "action": "submit"})  # nothing to send yet
        self.assertEqual(DevelopmentPlan.objects.get().status, "draft")
        self.post(self.emp_a_user, url, {"year": 2026, "action": "add", "competency": "Records management",
                                         "activity": "CSC records training"})
        self.post(self.emp_a_user, url, {"year": 2026, "action": "submit"})
        plan = DevelopmentPlan.objects.get()
        self.assertEqual((plan.status, plan.items.count()), ("submitted", 1))
        self.assertEqual(self.post(self.emp_a_user, url, {"year": 2026, "action": "review"}).status_code, 403)
        self.post(self.head_a, url, {"year": 2026, "action": "review", "review_note": "Good"})
        plan.refresh_from_db()
        self.assertEqual((plan.status, plan.reviewed_by), ("reviewed", self.head_a))
        self.assertEqual(self.get(self.staff_a, url).status_code, 403)
        self.assertEqual(self.get(self.head_a, f"/people/{self.person_b.pk}/development-plan/").status_code, 403)
        self.assertContains(self.get(self.hrmo_user, "/tracker/development-plans/", year="2026"), "1 reviewed")

    def test_workforce_profile_counts(self):
        from hr.talent_views import workforce_profile

        profile = workforce_profile(date(2026, 10, 28))
        self.assertEqual(profile["total"], 3)
        self.assertEqual(profile["sex"]["F"], 1)
        by_type = dict(profile["by_type"])
        self.assertEqual((by_type["Plantilla"]["total"], by_type["Job Order"]["F"]), (1, 1))
        self.assertEqual(self.get(self.auditor_user, "/reports/workforce/").status_code, 200)
        self.assertEqual(self.get(self.head_a, "/reports/workforce/").status_code, 403)

    def test_menus_render_for_every_role(self):
        for user in (self.hrmo_user, self.auditor_user, self.head_a, self.staff_a, self.emp_a_user, self.mayor):
            with self.subTest(user=user.username):
                self.assertEqual(self.get(user, "/").status_code, 200)
        page = self.get(self.hrmo_user, f"/people/{self.person_a.pk}/")
        self.assertContains(page, "Papers")
        self.assertContains(page, "Separation &amp; clearance")


class ModulesAndDeskTestCase(LguBase):
    """MD1-MD3, DK1-DK3: optional modules stay hidden until switched on; the HR Desk holds every tool."""

    MODULE_URLS = ["/pass-slips/", "/pass-slips/log/", "/awards/", "/development-plan/", "/tracker/development-plans/",
                   "/reports/workforce/"]

    def test_modules_off_by_default_answer_not_found_and_leave_no_links(self):
        for url in self.MODULE_URLS:
            with self.subTest(url=url):
                self.assertEqual(self.get(self.hrmo_user, url).status_code, 404)
        home = self.get(self.emp_a_user, "/")
        for text in ("My pass slips", "My development plan", "Awards &amp; recognition"):
            self.assertNotContains(home, text)
        desk = self.get(self.hrmo_user, "/desk/")
        for text in ("Pass slip log", "Development plans", "Awards &amp; recognition", "Workforce profile"):
            self.assertNotContains(desk, text)
        person = self.get(self.hrmo_user, f"/people/{self.person_a.pk}/")
        self.assertNotContains(person, "Development plan")
        self.assertNotContains(person, "Record a paper pass slip")

    @override_settings(HR_MODULES=["awards"])
    def test_switching_one_on_shows_only_that_one(self):
        self.assertEqual(self.get(self.hrmo_user, "/awards/").status_code, 200)
        self.assertEqual(self.get(self.hrmo_user, "/pass-slips/log/").status_code, 404)
        desk = self.get(self.hrmo_user, "/desk/")
        self.assertContains(desk, "Awards &amp; recognition")
        self.assertNotContains(desk, "Pass slip log")

    def test_unknown_module_name_is_a_programming_error(self):
        from hr import modules

        with self.assertRaises(ValueError):
            modules.enabled("payroll")

    def test_desk_groups_tools_and_counts_what_is_waiting(self):
        from hr import desk

        self.jo.end_date = date(2026, 8, 31)  # active but past its end: lapsed
        self.jo.save()
        cards = desk.cards(self.hrmo_user, date(2026, 10, 28))
        titles = [c["title"] for c in cards]
        self.assertEqual(titles[:2], ["Hiring and appointments", "Requests from employees"])
        renewals = next(i for c in cards for i in c["items"] if i["name"] == "tracker_renewals")
        self.assertEqual((renewals["count"], renewals["what"]), (1, "lapsed"))
        self.assertIn(renewals, desk.attention(cards))
        page = self.get(self.hrmo_user, "/desk/")
        self.assertContains(page, "Waiting now")
        self.assertContains(page, "Report on Appointments Issued")

    def test_tools_menu_is_the_desk_link_and_waiting_items_only(self):
        page = self.get(self.hrmo_user, "/")
        self.assertContains(page, "HR Desk: all tools")
        self.assertNotContains(page, ">Training hours<")  # on the desk, not in the menu

    def test_who_gets_the_desk(self):
        self.assertEqual(self.get(self.auditor_user, "/desk/").status_code, 200)
        auditor = self.get(self.auditor_user, "/desk/")
        self.assertNotContains(auditor, "Record overtime")  # HRMO records, the Auditor reads
        mayor = self.get(self.mayor, "/desk/")  # Executives already had Tools for the project list
        self.assertEqual([c["title"] for c in mayor.context["desk"]], ["Hiring and appointments"])
        self.assertContains(mayor, "Projects")
        self.assertNotContains(mayor, "Contract renewals")
        for user in (self.emp_a_user, self.head_a, self.staff_a):
            with self.subTest(user=user.username):
                self.assertEqual(self.get(user, "/desk/").status_code, 403)
                self.assertNotContains(self.get(user, "/"), "HR Desk: all tools")
