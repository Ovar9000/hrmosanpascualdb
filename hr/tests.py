from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from hr.models import (
    Delegate,
    Document,
    Engagement,
    LeaveCredit,
    LeaveRequest,
    LeaveType,
    Office,
    Person,
    Project,
)


class OfficeAndSeedOfficesTestCase(TestCase):
    def test_seed_offices_creates_25_offices_and_is_idempotent(self):
        call_command("seed_offices")
        self.assertEqual(Office.objects.count(), 25)

        # Check legislative offices
        ossb = Office.objects.get(acronym="OSSB")
        self.assertEqual(ossb.branch, Office.Branch.LEGISLATIVE)
        self.assertIsNotNone(ossb.parent)
        self.assertEqual(ossb.parent.name, "Office of the Sangguniang Bayan")

        # Check institution
        college = Office.objects.get(name="San Pascual Polytechnic College")
        self.assertEqual(college.type, Office.Type.INSTITUTION)

        # Idempotence: calling again updates without duplicate
        call_command("seed_offices")
        self.assertEqual(Office.objects.count(), 25)


class PersonModelTestCase(TestCase):
    def test_fake_id_constraint(self):
        # Valid fake ID starting with 00
        p = Person.objects.create(
            employee_no="SP-00001",
            last_name="Dela Cruz",
            first_name="Juan",
            birthdate=date(1990, 1, 1),
            tin="000-123-456-000",
        )
        self.assertEqual(str(p), "Dela Cruz, Juan")

        # Real/non-zero-prefixed ID should fail DB constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Person.objects.create(
                    employee_no="SP-00002",
                    last_name="Santos",
                    first_name="Maria",
                    birthdate=date(1992, 5, 10),
                    tin="123-456-789-000",  # Invalid for prototype
                )

    def test_duplicate_person_constraint(self):
        Person.objects.create(
            employee_no="SP-00001",
            last_name="Reyes",
            first_name="Pedro",
            birthdate=date(1985, 3, 15),
        )
        # Duplicate name + birthdate should raise IntegrityError
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Person.objects.create(
                    employee_no="SP-00002",
                    last_name="reyes",
                    first_name="PEDRO",
                    birthdate=date(1985, 3, 15),
                )


class EngagementModelTestCase(TestCase):
    def setUp(self):
        self.office = Office.objects.create(
            name="HRMO",
            branch=Office.Branch.EXECUTIVE,
            type=Office.Type.DEPARTMENT,
        )
        self.person = Person.objects.create(
            employee_no="SP-00010",
            last_name="Aquino",
            first_name="Corazon",
            birthdate=date(1980, 2, 2),
        )

    def test_plantilla_requires_sg_step_item(self):
        # Missing salary grade / step / item number on plantilla fails constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Engagement.objects.create(
                    person=self.person,
                    office=self.office,
                    type=Engagement.Type.PLANTILLA,
                    position_title="Administrative Officer V",
                    start_date=date(2020, 1, 1),
                )

        # Valid plantilla engagement succeeds
        eng = Engagement.objects.create(
            person=self.person,
            office=self.office,
            type=Engagement.Type.PLANTILLA,
            position_title="Administrative Officer V",
            salary_grade=18,
            step=1,
            item_number="SP-HRMO-001",
            rate=Decimal("46725.00"),
            rate_basis=Engagement.RateBasis.MONTHLY,
            start_date=date(2020, 1, 1),
        )
        self.assertIsNotNone(eng.pk)

    def test_non_plantilla_forbids_salary_grade(self):
        # Job order with salary grade fails constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Engagement.objects.create(
                    person=self.person,
                    office=self.office,
                    type=Engagement.Type.JOB_ORDER,
                    position_title="Office Clerk",
                    salary_grade=5,
                    step=1,
                    item_number="SP-JO-01",
                    start_date=date(2022, 1, 1),
                )

    def test_project_based_requires_project(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Engagement.objects.create(
                    person=self.person,
                    office=self.office,
                    type=Engagement.Type.PROJECT_BASED,
                    position_title="Site Inspector",
                    start_date=date(2023, 1, 1),
                    project=None,
                )

        project = Project.objects.create(
            name="Municipal Hall Renovation",
            office=self.office,
            start_date=date(2023, 1, 1),
        )
        eng = Engagement.objects.create(
            person=self.person,
            office=self.office,
            type=Engagement.Type.PROJECT_BASED,
            position_title="Site Inspector",
            start_date=date(2023, 1, 1),
            project=project,
        )
        self.assertIsNotNone(eng.pk)


class DocumentAndLeaveTestCase(TestCase):
    def setUp(self):
        self.office = Office.objects.create(
            name="Office of the Mayor",
            branch=Office.Branch.EXECUTIVE,
            type=Office.Type.DEPARTMENT,
        )
        self.person = Person.objects.create(
            employee_no="SP-00020",
            last_name="Bautista",
            first_name="Herbert",
            birthdate=date(1975, 4, 12),
        )

    def test_document_xor_person_engagement(self):
        eng = Engagement.objects.create(
            person=self.person,
            office=self.office,
            type=Engagement.Type.CASUAL,
            position_title="Staff",
            start_date=date(2021, 1, 1),
        )
        # Cannot attach to both person and engagement
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Document.objects.create(
                    person=self.person,
                    engagement=eng,
                    doc_type=Document.Type.PDS,
                )

        # Can attach to person alone
        doc = Document.objects.create(
            person=self.person,
            doc_type=Document.Type.PDS,
            physical_location="Cabinet 1, Folder A",
        )
        self.assertIsNotNone(doc.pk)

    def test_leave_credit_validation(self):
        # Person without active plantilla or casual engagement cannot have leave credit
        credit = LeaveCredit(
            person=self.person,
            leave_type=LeaveType.VACATION,
            days=Decimal("15.0"),
            as_of=date(2026, 1, 1),
        )
        with self.assertRaises(ValidationError):
            credit.clean()

        # Add casual engagement -> clean() passes
        Engagement.objects.create(
            person=self.person,
            office=self.office,
            type=Engagement.Type.CASUAL,
            position_title="Utility Worker",
            start_date=date(2026, 1, 1),
            status=Engagement.Status.ACTIVE,
        )
        credit.clean()  # should not raise


class SeedFakeCommandTestCase(TestCase):
    def test_seed_fake_command(self):
        call_command("seed_fake", "--clear")

        # Scaled headcount: 420 people matching survey composition
        people_count = Person.objects.count()
        self.assertEqual(people_count, 420)

        # Active engagement breakdown matches survey findings
        active_eng = Engagement.objects.filter(status=Engagement.Status.ACTIVE)
        self.assertEqual(active_eng.filter(type=Engagement.Type.PLANTILLA).count(), 94)
        self.assertEqual(active_eng.filter(type=Engagement.Type.CASUAL).count(), 28)
        self.assertEqual(active_eng.filter(type=Engagement.Type.ELECTED).count(), 12)
        jo_cos_count = active_eng.filter(
            type__in=[Engagement.Type.JOB_ORDER, Engagement.Type.CONTRACT_OF_SERVICE, Engagement.Type.PROJECT_BASED]
        ).count()
        self.assertGreaterEqual(jo_cos_count, 280)

        # All fake ID numbers start with "00"
        for p in Person.objects.all():
            if p.tin:
                self.assertTrue(p.tin.startswith("00"), f"TIN {p.tin} does not start with 00")
            if p.gsis_no:
                self.assertTrue(p.gsis_no.startswith("00"), f"GSIS {p.gsis_no} does not start with 00")

        # Career progression check: some people have multiple engagements (JO -> Casual -> Plantilla)
        people_with_multi_engagements = Person.objects.filter(engagements__status=Engagement.Status.ENDED).distinct()
        self.assertGreaterEqual(people_with_multi_engagements.count(), 10)

        # Projects and Documents exist
        self.assertEqual(Project.objects.count(), 6)
        self.assertGreaterEqual(Document.objects.count(), 60)

        # Leave requests exist
        self.assertGreaterEqual(LeaveRequest.objects.count(), 20)


class ViewsTestCase(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(username="staffuser", password="securepassword123", is_staff=True)
        self.regular_user = User.objects.create_user(username="reguser", password="securepassword123", is_staff=False)

        call_command("seed_offices")
        call_command("seed_fake")

    def test_views_require_login(self):
        # Unauthenticated users redirected to login
        resp = self.client.get("/offices/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

        resp = self.client.get("/people/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/accounts/login/", resp.url)

    def test_office_views(self):
        self.client.force_login(self.user)
        resp = self.client.get("/offices/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Municipal Offices Directory")
        self.assertContains(resp, "Office of the Municipal Mayor")

        # Filter by branch
        resp_leg = self.client.get("/offices/?branch=legislative")
        self.assertEqual(resp_leg.status_code, 200)
        self.assertContains(resp_leg, "Office of the Sangguniang Bayan")
        self.assertNotContains(resp_leg, "Office of the Municipal Mayor")

        # Office detail
        office = Office.objects.first()
        resp_det = self.client.get(f"/offices/{office.pk}/")
        self.assertEqual(resp_det.status_code, 200)
        self.assertContains(resp_det, office.name)

    def test_person_views(self):
        self.client.force_login(self.user)
        resp = self.client.get("/people/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Personnel Directory")
        self.assertEqual(len(resp.context["page_obj"]), 25)

        person = Person.objects.first()
        resp_det = self.client.get(f"/people/{person.pk}/")
        self.assertEqual(resp_det.status_code, 200)
        self.assertContains(resp_det, person.last_name)
        self.assertContains(resp_det, person.employee_no)
        # Staff user can see rates
        self.assertTrue(resp_det.context["can_view_rates"])

    def test_rate_visibility_for_non_staff(self):
        self.client.force_login(self.regular_user)
        person = Person.objects.first()
        resp_det = self.client.get(f"/people/{person.pk}/")
        self.assertEqual(resp_det.status_code, 200)
        # Regular non-staff user does not see rates
        self.assertFalse(resp_det.context["can_view_rates"])
        self.assertContains(resp_det, "Pay rates are restricted")


class Step4AModelAndAdminTestCase(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.superuser = User.objects.create_superuser("superadmin", "super@test.com", "superpass123")
        self.staff_sysadmin = User.objects.create_user("sysadmin", "sys@test.com", "syspass123", is_staff=True)
        self.mayor = User.objects.create_user("mayor", "mayor@test.com", "mayorpass123")
        self.delegate_user = User.objects.create_user("delegate", "delegate@test.com", "delpass123")

        self.office = Office.objects.create(name="Office of the Mayor", branch=Office.Branch.EXECUTIVE, type=Office.Type.DEPARTMENT)
        self.person = Person.objects.create(
            employee_no="SP-00099",
            last_name="Santiago",
            first_name="Miriam",
            birthdate=date(1970, 6, 15),
        )

    def test_engagement_leave_eligible_defaults_and_toggle(self):
        # Plantilla defaults leave_eligible=True
        plantilla = Engagement.objects.create(
            person=self.person,
            office=self.office,
            type=Engagement.Type.PLANTILLA,
            position_title="Attorney IV",
            salary_grade=23,
            step=1,
            item_number="SP-MO-099",
            start_date=date(2025, 1, 1),
        )
        self.assertTrue(plantilla.leave_eligible)
        self.assertTrue(self.person.earns_leave_credits())

        # Casual defaults leave_eligible=True
        casual = Engagement.objects.create(
            person=self.person,
            office=self.office,
            type=Engagement.Type.CASUAL,
            position_title="Legal Aide",
            start_date=date(2025, 1, 1),
        )
        self.assertTrue(casual.leave_eligible)

        # Job order defaults leave_eligible=False
        jo = Engagement.objects.create(
            person=self.person,
            office=self.office,
            type=Engagement.Type.JOB_ORDER,
            position_title="Office Encoder",
            start_date=date(2025, 1, 1),
        )
        self.assertFalse(jo.leave_eligible)

        # HRMO can explicitly toggle leave_eligible on JO
        jo.leave_eligible = True
        jo.save()
        self.assertTrue(jo.leave_eligible)

    def test_document_original_on_file(self):
        doc = Document.objects.create(
            person=self.person,
            doc_type=Document.Type.PDS,
            physical_location="Cabinet 1, Drawer 2",
        )
        self.assertTrue(doc.original_on_file)

    def test_delegate_model_and_active_window(self):
        d = Delegate.objects.create(
            principal=self.mayor,
            delegate=self.delegate_user,
            valid_from=date(2026, 10, 1),
            valid_to=date(2026, 10, 15),
            notes="Covering Mayor leave",
        )
        self.assertTrue(d.is_active(date(2026, 10, 5)))
        self.assertFalse(d.is_active(date(2026, 10, 20)))  # Expired
        self.assertFalse(d.is_active(date(2026, 9, 28)))   # Prior

        # valid_to < valid_from constraint
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Delegate.objects.create(
                    principal=self.mayor,
                    delegate=self.delegate_user,
                    valid_from=date(2026, 10, 20),
                    valid_to=date(2026, 10, 10),
                )

    def test_admin_hr_models_forbidden_for_non_superusers(self):
        # Non-superuser (System Admin) accessing HR models in /admin/ gets 403
        self.client.force_login(self.staff_sysadmin)
        resp = self.client.get("/admin/hr/person/")
        self.assertEqual(resp.status_code, 403)

        # Superuser can access /admin/hr/person/
        self.client.force_login(self.superuser)
        resp_super = self.client.get("/admin/hr/person/")
        self.assertEqual(resp_super.status_code, 200)



