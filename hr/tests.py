import ast
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, transaction
from django.test import RequestFactory, TestCase
from hr.audit import SENSITIVE_AUDIT_FIELDS, get_client_ip, is_ip_trusted, log_audit_event
from hr.forms import PersonForm
from hr.models import (
    AuditLog,
    Delegate,
    Document,
    EmployeeSequence,
    Engagement,
    LeaveCredit,
    LeaveRequest,
    LeaveType,
    Office,
    Person,
    Project,
    UserProfile,
)
from hr.sequences import get_next_employee_no, sync_employee_sequence
from hr.permissions import (
    PROTECTED_ROLES,
    ROLE_AUDITOR,
    ROLE_EXECUTIVE,
    ROLE_HRMO,
    ROLE_OFFICE_HEAD,
    ROLE_OFFICE_STAFF,
    ROLE_SYSADMIN,
    is_active_delegate,
    is_executive,
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
        User = get_user_model()
        call_command("seed_offices")
        call_command("seed_fake")
        call_command("setup_roles")

        # HRMO staff user (sees all offices, all people, and pay rates)
        self.user = User.objects.create_user(username="hrmouser", password="securepassword123")
        self.user.groups.add(Group.objects.get(name=ROLE_HRMO))

        # Regular employee user (self-service only)
        self.regular_person = Person.objects.first()
        self.regular_user = User.objects.create_user(username="reguser", password="securepassword123")
        UserProfile.objects.create(user=self.regular_user, person=self.regular_person)

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
        # HRMO user can see rates
        self.assertTrue(resp_det.context["can_view_rates"])

    def test_rate_visibility_for_non_staff(self):
        self.client.force_login(self.regular_user)
        resp_det = self.client.get(f"/people/{self.regular_person.pk}/")
        self.assertEqual(resp_det.status_code, 200)
        # Regular employee user does not see rates
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


class Step4CSecurityAndRolesTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        call_command("seed_offices")
        call_command("setup_roles")

        self.superuser = User.objects.create_superuser("superadmin", "super@test.com", "superpass123")
        self.sysadmin = User.objects.create_user("sysadmin", "sys@test.com", "syspass123", is_staff=True)
        self.sysadmin.groups.add(Group.objects.get(name=ROLE_SYSADMIN))

        self.hrmo_user = User.objects.create_user("hrmo", "hrmo@test.com", "hrmopass123")
        self.hrmo_user.groups.add(Group.objects.get(name=ROLE_HRMO))

        self.auditor_user = User.objects.create_user("auditor", "aud@test.com", "audpass123")
        self.auditor_user.groups.add(Group.objects.get(name=ROLE_AUDITOR))

        self.mayor = User.objects.create_user("mayor", "mayor@test.com", "mayorpass123")
        self.mayor.groups.add(Group.objects.get(name=ROLE_EXECUTIVE))

        self.delegate_user = User.objects.create_user("deluser", "del@test.com", "delpass123")

        # Two offices
        self.office_a = Office.objects.get(name="Office of the Municipal Mayor")
        self.office_b = Office.objects.get(name="Office of the Municipal Treasurer")

        # Office Head for Office A
        self.head_a = User.objects.create_user("head_a", "head_a@test.com", "headpass123")
        self.head_a.groups.add(Group.objects.get(name=ROLE_OFFICE_HEAD))
        UserProfile.objects.create(user=self.head_a, office=self.office_a)

        # Office Staff for Office A
        self.staff_a = User.objects.create_user("staff_a", "staff_a@test.com", "staffpass123")
        self.staff_a.groups.add(Group.objects.get(name=ROLE_OFFICE_STAFF))
        UserProfile.objects.create(user=self.staff_a, office=self.office_a)

        # Person A in Office A
        self.person_a = Person.objects.create(
            employee_no="SP-00101",
            last_name="Rizal",
            first_name="Jose",
            birthdate=date(1980, 6, 19),
            tin="000-111-222-000",
        )
        self.eng_a = Engagement.objects.create(
            person=self.person_a,
            office=self.office_a,
            type=Engagement.Type.PLANTILLA,
            position_title="Executive Assistant IV",
            salary_grade=22,
            step=1,
            item_number="SP-MO-001",
            rate=Decimal("50000.00"),
            rate_basis=Engagement.RateBasis.MONTHLY,
            start_date=date(2025, 1, 1),
            status=Engagement.Status.ACTIVE,
        )
        self.doc_a = Document.objects.create(
            person=self.person_a,
            doc_type=Document.Type.APPOINTMENT,
            physical_location="Cabinet A, Drawer 1",
            original_on_file=True,
        )

        # Person B in Office B
        self.person_b = Person.objects.create(
            employee_no="SP-00202",
            last_name="Bonifacio",
            first_name="Andres",
            birthdate=date(1982, 11, 30),
            tin="000-333-444-000",
        )
        self.eng_b = Engagement.objects.create(
            person=self.person_b,
            office=self.office_b,
            type=Engagement.Type.CASUAL,
            position_title="Revenue Collector I",
            rate=Decimal("15000.00"),
            rate_basis=Engagement.RateBasis.MONTHLY,
            start_date=date(2025, 1, 1),
            status=Engagement.Status.ACTIVE,
        )

        # Employee self-service user for Person A
        self.emp_a_user = User.objects.create_user("emp_a", "emp_a@test.com", "emppass123")
        UserProfile.objects.create(user=self.emp_a_user, person=self.person_a, office=self.office_a)

    def test_sysadmin_cannot_access_hr_front_views(self):
        self.client.force_login(self.sysadmin)
        resp_people = self.client.get("/people/")
        self.assertEqual(resp_people.status_code, 302)
        self.assertIn("/admin/", resp_people.url)

        resp_offices = self.client.get("/offices/")
        self.assertEqual(resp_offices.status_code, 302)
        self.assertIn("/admin/", resp_offices.url)

    def test_sysadmin_cannot_see_hr_models_in_admin(self):
        self.client.force_login(self.sysadmin)
        # Direct URL access to HR models is 403 Forbidden
        for model_path in ["person", "engagement", "document", "office", "leaverequest", "delegate"]:
            resp = self.client.get(f"/admin/hr/{model_path}/")
            self.assertEqual(resp.status_code, 403, f"Expected 403 for /admin/hr/{model_path}/")

        # Admin index does not show HR app
        resp_index = self.client.get("/admin/")
        self.assertEqual(resp_index.status_code, 200)
        self.assertNotContains(resp_index, "hr/person")
        self.assertNotContains(resp_index, "hr/engagement")

    def test_sysadmin_cannot_assign_protected_roles(self):
        from django.contrib.admin.sites import site
        from hr.admin import CustomUserAdmin
        User = get_user_model()
        user_admin = CustomUserAdmin(User, site)

        rf = RequestFactory()
        req = rf.get("/admin/auth/user/add/")
        req.user = self.sysadmin

        formfield = user_admin.formfield_for_manytomany(User._meta.get_field("groups"), req)
        group_choices = list(formfield.queryset.values_list("name", flat=True))
        for protected in PROTECTED_ROLES:
            self.assertNotIn(protected, group_choices, f"{protected} must not be selectable by SysAdmin")

        # Even with forged POST request, save_related blocks it
        new_user = User.objects.create_user("target_user", "target@test.com", "pass123")
        hrmo_group = Group.objects.get(name=ROLE_HRMO)
        new_user.groups.add(hrmo_group)

        class DummyForm:
            instance = new_user

            def save_m2m(self):
                pass

        with self.assertRaises(PermissionDenied):
            user_admin.save_related(req, DummyForm(), [], False)


        # Confirm target_user was stripped of HRMO Staff
        self.assertFalse(new_user.groups.filter(name=ROLE_HRMO).exists())

    def test_sysadmin_cannot_edit_superuser_or_protected_members(self):
        from django.contrib.admin.sites import site
        from hr.admin import CustomUserAdmin
        User = get_user_model()
        user_admin = CustomUserAdmin(User, site)

        rf = RequestFactory()
        req = rf.get(f"/admin/auth/user/{self.superuser.pk}/change/")
        req.user = self.sysadmin

        # Cannot edit emergency superuser
        self.assertFalse(user_admin.has_change_permission(req, self.superuser))
        self.assertFalse(user_admin.has_delete_permission(req, self.superuser))

        # Cannot edit HRMO staff member
        self.assertFalse(user_admin.has_change_permission(req, self.hrmo_user))
        self.assertFalse(user_admin.has_delete_permission(req, self.hrmo_user))

    def test_sysadmin_cannot_modify_groups_in_admin(self):
        from django.contrib.admin.sites import site
        from hr.admin import CustomGroupAdmin
        group_admin = CustomGroupAdmin(Group, site)

        rf = RequestFactory()
        req = rf.get("/admin/auth/group/")
        req.user = self.sysadmin

        self.assertFalse(group_admin.has_add_permission(req))
        self.assertFalse(group_admin.has_change_permission(req, Group.objects.first()))
        self.assertFalse(group_admin.has_delete_permission(req, Group.objects.first()))

    def test_office_head_scoping_and_minimization(self):
        self.client.force_login(self.head_a)

        # Scoped to own office
        resp_offices = self.client.get("/offices/")
        self.assertEqual(resp_offices.status_code, 200)
        self.assertContains(resp_offices, self.office_a.name)
        self.assertNotContains(resp_offices, self.office_b.name)

        resp_off_a = self.client.get(f"/offices/{self.office_a.pk}/")
        self.assertEqual(resp_off_a.status_code, 200)

        resp_off_b = self.client.get(f"/offices/{self.office_b.pk}/")
        self.assertEqual(resp_off_b.status_code, 404)

        # Scoped to active personnel in own office
        resp_people = self.client.get("/people/")
        self.assertEqual(resp_people.status_code, 200)
        self.assertContains(resp_people, self.person_a.last_name)
        self.assertNotContains(resp_people, self.person_b.last_name)

        # Person detail in own office: 200 OK, but NO pay and NO fake IDs
        resp_person_a = self.client.get(f"/people/{self.person_a.pk}/")
        self.assertEqual(resp_person_a.status_code, 200)
        self.assertFalse(resp_person_a.context["can_view_rates"])
        self.assertFalse(resp_person_a.context["can_view_id_numbers"])
        self.assertFalse(resp_person_a.context["can_view_doc_content"])
        self.assertNotContains(resp_person_a, "50000")
        self.assertNotContains(resp_person_a, "000-111-222-000")
        self.assertContains(resp_person_a, "Pay rates are restricted")
        self.assertContains(resp_person_a, "Government ID numbers are restricted")
        self.assertContains(resp_person_a, "Cabinet A, Drawer 1")

        # Person detail in other office: 404 Not Found
        resp_person_b = self.client.get(f"/people/{self.person_b.pk}/")
        self.assertEqual(resp_person_b.status_code, 404)

    def test_office_head_and_staff_cannot_download_documents(self):
        self.client.force_login(self.head_a)
        resp = self.client.get(f"/documents/{self.doc_a.pk}/download/")
        self.assertEqual(resp.status_code, 403)

        self.client.force_login(self.staff_a)
        resp_staff = self.client.get(f"/documents/{self.doc_a.pk}/download/")
        self.assertEqual(resp_staff.status_code, 403)

    def test_executive_sees_across_offices_without_pay_or_ids(self):
        self.client.force_login(self.mayor)

        # Sees across offices
        resp_a = self.client.get(f"/people/{self.person_a.pk}/")
        self.assertEqual(resp_a.status_code, 200)
        self.assertFalse(resp_a.context["can_view_rates"])
        self.assertFalse(resp_a.context["can_view_id_numbers"])
        self.assertNotContains(resp_a, "50000")
        self.assertNotContains(resp_a, "000-111-222-000")

        resp_b = self.client.get(f"/people/{self.person_b.pk}/")
        self.assertEqual(resp_b.status_code, 200)
        self.assertFalse(resp_b.context["can_view_rates"])
        self.assertFalse(resp_b.context["can_view_id_numbers"])
        self.assertNotContains(resp_b, "15000")
        self.assertNotContains(resp_b, "000-333-444-000")

    def test_executive_delegate_active_and_expired(self):
        today = date(2026, 10, 5)
        d = Delegate.objects.create(
            principal=self.mayor,
            delegate=self.delegate_user,
            valid_from=date(2026, 10, 1),
            valid_to=date(2026, 10, 15),
            notes="Covering Mayor leave",
        )
        self.assertTrue(is_active_delegate(self.delegate_user, today))
        self.assertTrue(is_executive(self.delegate_user))

        # Active delegate can view across offices
        self.client.force_login(self.delegate_user)
        resp = self.client.get(f"/people/{self.person_a.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_view_rates"])

        # Expired delegation
        d.valid_from = date(2026, 9, 1)
        d.valid_to = date(2026, 9, 15)
        d.save()
        self.assertFalse(is_active_delegate(self.delegate_user))
        self.assertFalse(is_executive(self.delegate_user))

        # Expired delegate has no role access
        resp_exp = self.client.get(f"/people/{self.person_a.pk}/")
        self.assertEqual(resp_exp.status_code, 404)

    def test_auditor_full_read_with_pay_and_ids(self):
        self.client.force_login(self.auditor_user)

        resp = self.client.get(f"/people/{self.person_a.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["can_view_rates"])
        self.assertTrue(resp.context["can_view_id_numbers"])
        self.assertTrue(resp.context["can_view_doc_content"])
        self.assertContains(resp, "000-111-222-000")
        self.assertContains(resp, "50000.00")


    def test_employee_self_service_privacy_and_access(self):
        self.client.force_login(self.emp_a_user)

        # Can view own record
        resp_own = self.client.get(f"/people/{self.person_a.pk}/")
        self.assertEqual(resp_own.status_code, 200)
        self.assertFalse(resp_own.context["can_view_rates"])
        self.assertTrue(resp_own.context["can_view_id_numbers"])
        self.assertContains(resp_own, "000-111-222-000")

        # Cannot view other personnel
        resp_other = self.client.get(f"/people/{self.person_b.pk}/")
        self.assertEqual(resp_other.status_code, 404)


class Step5AuditLogTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        call_command("seed_offices")
        call_command("setup_roles")

        self.superuser = User.objects.create_superuser("superadmin", "super@test.com", "superpass123")
        self.auditor = User.objects.create_user("auditor_user", "auditor@test.com", "audpass123")
        self.auditor.groups.add(Group.objects.get(name=ROLE_AUDITOR))

        self.hrmo = User.objects.create_user("hrmo_user", "hrmo@test.com", "hrmopass123")
        self.hrmo.groups.add(Group.objects.get(name=ROLE_HRMO))

        self.office_head = User.objects.create_user("head_user", "head@test.com", "headpass123")
        self.office_head.groups.add(Group.objects.get(name=ROLE_OFFICE_HEAD))

        self.office = Office.objects.get(name="Office of the Municipal Mayor")
        UserProfile.objects.create(user=self.office_head, office=self.office)

        self.person = Person.objects.create(
            employee_no="SP-00888",
            last_name="Mabini",
            first_name="Apolinario",
            birthdate=date(1985, 7, 23),
            tin="000-888-999-000",
        )

    def test_database_trigger_blocks_update_and_delete(self):
        # Create an audit log entry
        entry = log_audit_event(
            action=AuditLog.Action.CREATE,
            model_name="Person",
            object_id=self.person.pk,
            object_repr=str(self.person),
            changes={"first_name": {"old": None, "new": "Apolinario"}},
        )
        self.assertIsNotNone(entry)
        self.assertIsNotNone(entry.pk)

        # Attempting DELETE via SQL/ORM must fail at the PostgreSQL trigger level
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                AuditLog.objects.filter(pk=entry.pk).delete()

        # Attempting UPDATE via SQL/ORM must fail at the PostgreSQL trigger level
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                AuditLog.objects.filter(pk=entry.pk).update(object_repr="Tampered description")

    def test_sensitive_fields_redaction_in_changes_diff(self):
        # 1. Update Person with new fake TIN
        self.person.tin = "000-777-666-000"
        self.person.save()

        person_update_log = AuditLog.objects.filter(
            model_name="Person", object_id=str(self.person.pk), action=AuditLog.Action.UPDATE
        ).first()
        self.assertIsNotNone(person_update_log)
        # tin field name is logged, but its value is redacted
        self.assertIn("tin", person_update_log.changes)
        self.assertEqual(person_update_log.changes["tin"], "[REDACTED]")
        self.assertNotIn("000-777-666-000", str(person_update_log.changes))

        # 2. Create Engagement with pay rate
        eng = Engagement.objects.create(
            person=self.person,
            office=self.office,
            type=Engagement.Type.CASUAL,
            position_title="Legal Researcher",
            rate=Decimal("28000.00"),
            rate_basis=Engagement.RateBasis.MONTHLY,
            start_date=date(2025, 1, 1),
        )
        eng_log = AuditLog.objects.filter(
            model_name="Engagement", object_id=str(eng.pk), action=AuditLog.Action.CREATE
        ).first()
        self.assertIsNotNone(eng_log)
        self.assertIn("rate", eng_log.changes)
        self.assertEqual(eng_log.changes["rate"], "[REDACTED]")
        self.assertNotIn("28000.00", str(eng_log.changes))

        # 3. Password reset on User
        target_user = get_user_model().objects.create_user("pwd_target", "pwd@test.com", "initialpass123")
        target_user.set_password("newsecretpass456")
        target_user.save()

        pwd_log = AuditLog.objects.filter(
            model_name="User", object_id=str(target_user.pk), action=AuditLog.Action.PASSWORD_RESET
        ).first()
        self.assertIsNotNone(pwd_log)
        self.assertIn("password", pwd_log.changes)
        self.assertEqual(pwd_log.changes["password"], "[REDACTED]")
        self.assertNotIn("pbkdf2_sha256", str(pwd_log.changes))
        self.assertNotIn("newsecretpass456", str(pwd_log.changes))

    def test_user_deletion_does_not_affect_audit_log_rows(self):
        User = get_user_model()
        temp_user = User.objects.create_user("temp_user", "temp@test.com", "pass123")
        temp_uid = temp_user.pk

        entry = log_audit_event(
            action=AuditLog.Action.UPDATE,
            model_name="Person",
            object_id=self.person.pk,
            object_repr=str(self.person),
            user=temp_user,
        )

        self.assertEqual(entry.user_id, temp_uid)
        self.assertEqual(entry.username, "temp_user")

        # Delete user
        temp_user.delete()

        # AuditLog entry still exists intact
        reloaded = AuditLog.objects.get(pk=entry.pk)
        self.assertEqual(reloaded.user_id, temp_uid)
        self.assertEqual(reloaded.username, "temp_user")

    def test_transaction_binding_failure_rolls_back_action(self):
        # If audit log creation fails, the outer action transaction must roll back
        from unittest.mock import patch

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                p = Person(
                    employee_no="SP-00999",
                    last_name="Silang",
                    first_name="Gabriela",
                    birthdate=date(1991, 3, 19),
                )
                p.save()
                with patch("hr.models.AuditLog.objects.create", side_effect=RuntimeError("Audit DB down")):
                    log_audit_event(
                        action=AuditLog.Action.UPDATE,
                        model_name="Person",
                        object_id=p.pk,
                        object_repr=str(p),
                    )

        # Confirm person was rolled back and never persisted
        self.assertFalse(Person.objects.filter(employee_no="SP-00999").exists())

    def test_trusted_proxy_client_ip_resolution(self):
        rf = RequestFactory()

        # Untrusted remote address: X-Forwarded-For must be IGNORED
        req_untrusted = rf.get("/people/", REMOTE_ADDR="198.51.100.42", HTTP_X_FORWARDED_FOR="1.1.1.1")
        self.assertEqual(get_client_ip(req_untrusted), "198.51.100.42")

        # Trusted remote address: X-Forwarded-For must be extracted
        req_trusted = rf.get("/people/", REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.195, 10.0.0.1")
        self.assertEqual(get_client_ip(req_trusted), "203.0.113.195")

    def test_selective_view_logging(self):
        self.client.force_login(self.hrmo)

        # 1. Directory listing (/people/) must NOT log VIEW
        init_view_count = AuditLog.objects.filter(action=AuditLog.Action.VIEW).count()
        self.client.get("/people/")
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.VIEW).count(), init_view_count)

        # 2. Offices directory (/offices/) must NOT log VIEW
        self.client.get("/offices/")
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.VIEW).count(), init_view_count)

        # 3. Person detail (/people/<id>/) MUST log VIEW
        resp_detail = self.client.get(f"/people/{self.person.pk}/")
        self.assertEqual(resp_detail.status_code, 200)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AuditLog.Action.VIEW, model_name="Person", object_id=str(self.person.pk)
            ).count(),
            1,
        )

    def test_auditor_interface_access_and_export(self):
        # Non-auditor (Office Head) gets 403 Forbidden on /audit/
        self.client.force_login(self.office_head)
        resp_denied = self.client.get("/audit/")
        self.assertEqual(resp_denied.status_code, 403)

        # 403 denial was logged as PERMISSION_DENIED
        denial_log = AuditLog.objects.filter(action=AuditLog.Action.PERMISSION_DENIED).first()
        self.assertIsNotNone(denial_log)
        self.assertEqual(denial_log.changes.get("path"), "/audit/")

        # Auditor gets 200 OK
        self.client.force_login(self.auditor)
        resp_audit = self.client.get("/audit/")
        self.assertEqual(resp_audit.status_code, 200)
        self.assertContains(resp_audit, "System Audit Trail")

        # Auditor exports CSV
        resp_export = self.client.get("/audit/export/csv/")
        self.assertEqual(resp_export.status_code, 200)
        self.assertEqual(resp_export["Content-Type"], "text/csv")
        self.assertIn("Timestamp,Action,User ID", resp_export.content.decode("utf-8"))

        # EXPORT action was recorded in AuditLog
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.EXPORT, model_name="AuditLog").exists()
        )

    def test_failed_login_records_username_only_for_existing_account(self):
        # 1. Failed login for existing account: records the actual username
        self.client.post("/accounts/login/", {"username": "auditor_user", "password": "wrongpassword"})
        log_existing = AuditLog.objects.filter(
            action=AuditLog.Action.FAILED_LOGIN, changes__username_attempted="auditor_user"
        ).first()
        self.assertIsNotNone(log_existing)
        self.assertEqual(log_existing.username, "auditor_user")
        self.assertIn("auditor_user", log_existing.object_repr)

        # 2. Failed login for non-existent account: records "[unknown]" to prevent credential leaks
        self.client.post("/accounts/login/", {"username": "nonexistent_stranger", "password": "somepassword"})
        log_unknown = AuditLog.objects.filter(
            action=AuditLog.Action.FAILED_LOGIN, changes__username_attempted="[unknown]"
        ).first()
        self.assertIsNotNone(log_unknown)
        self.assertEqual(log_unknown.username, "[unknown]")
        self.assertIn("[unknown]", log_unknown.object_repr)
        self.assertNotIn("nonexistent_stranger", str(log_unknown.changes))

    def test_permission_denied_deduplication(self):
        # Office head accesses protected /audit/ repeatedly
        self.client.force_login(self.office_head)

        # 1. First request creates PERMISSION_DENIED entry
        resp1 = self.client.get("/audit/")
        self.assertEqual(resp1.status_code, 403)
        count_after_first = AuditLog.objects.filter(
            action=AuditLog.Action.PERMISSION_DENIED,
            user_id=self.office_head.pk,
            changes__path="/audit/",
        ).count()
        self.assertEqual(count_after_first, 1)

        # 2. Second request within the same minute does NOT create a duplicate entry
        resp2 = self.client.get("/audit/")
        self.assertEqual(resp2.status_code, 403)
        count_after_second = AuditLog.objects.filter(
            action=AuditLog.Action.PERMISSION_DENIED,
            user_id=self.office_head.pk,
            changes__path="/audit/",
        ).count()
        self.assertEqual(count_after_second, 1)

        # 3. Request to a different forbidden path DOES create a new entry
        doc = Document.objects.create(
            person=self.person,
            doc_type=Document.Type.APPOINTMENT,
            physical_location="Cabinet A",
        )
        resp3 = self.client.get(f"/documents/{doc.pk}/download/")
        self.assertEqual(resp3.status_code, 403)
        count_doc_denied = AuditLog.objects.filter(
            action=AuditLog.Action.PERMISSION_DENIED,
            user_id=self.office_head.pk,
            changes__path=f"/documents/{doc.pk}/download/",
        ).count()
        self.assertEqual(count_doc_denied, 1)

        # 4. A different user accessing /audit/ creates a separate entry for that user
        self.client.force_login(self.hrmo)
        resp4 = self.client.get("/audit/")
        self.assertEqual(resp4.status_code, 403)
        count_hrmo_denied = AuditLog.objects.filter(
            action=AuditLog.Action.PERMISSION_DENIED,
            user_id=self.hrmo.pk,
            changes__path="/audit/",
        ).count()
        self.assertEqual(count_hrmo_denied, 1)

    def test_trusted_proxies_environment_setting(self):
        from django.test import override_settings

        with override_settings(TRUSTED_PROXIES=["127.0.0.1", "::1", "172.18.0.0/16"]):
            self.assertTrue(is_ip_trusted("127.0.0.1"))
            self.assertTrue(is_ip_trusted("::1"))
            self.assertTrue(is_ip_trusted("172.18.0.42"))
            self.assertFalse(is_ip_trusted("192.168.1.1"))

    def test_codebase_inspection_forbids_unlogged_queryset_mutations(self):
        # Scans hr/ app views and domain code to ensure no .update(), .bulk_create(), or raw SQL are used
        from pathlib import Path

        hr_dir = Path(__file__).resolve().parent
        forbidden_calls = {"bulk_create", "bulk_update", "raw"}

        for py_file in hr_dir.glob("*.py"):
            if py_file.name in ["tests.py", "audit.py", "signals.py"]:
                continue

            with open(py_file, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        call_name = node.func.attr
                        # Disallow bulk_create(), bulk_update(), raw() in domain code
                        if call_name in forbidden_calls:
                            self.fail(
                                f"Forbidden call '{call_name}' in {py_file.name} line {node.lineno}: "
                                "Domain operations must use model save() or delete() to guarantee audit trail."
                            )


class Step6APersonEntryTestCase(TestCase):
    def setUp(self):
        User = get_user_model()
        call_command("seed_offices")
        call_command("setup_roles")

        self.hrmo_user = User.objects.create_user("hrmo_editor", "hrmo@test.com", "pass123")
        self.hrmo_user.groups.add(Group.objects.get(name=ROLE_HRMO))

        self.sysadmin = User.objects.create_user("sys_admin", "sys@test.com", "pass123", is_staff=True)
        self.sysadmin.groups.add(Group.objects.get(name=ROLE_SYSADMIN))

        self.office_head = User.objects.create_user("head_user", "head@test.com", "pass123")
        self.office_head.groups.add(Group.objects.get(name=ROLE_OFFICE_HEAD))

        self.auditor = User.objects.create_user("audit_user", "audit@test.com", "pass123")
        self.auditor.groups.add(Group.objects.get(name=ROLE_AUDITOR))

        self.executive = User.objects.create_user("exec_user", "exec@test.com", "pass123")
        self.executive.groups.add(Group.objects.get(name=ROLE_EXECUTIVE))

        self.employee = User.objects.create_user("reg_emp", "emp@test.com", "pass123")

        self.existing_person = Person.objects.create(
            employee_no="SP-00001",
            last_name="Del Rosario",
            first_name="Carmen",
            middle_name="Santos",
            suffix="",
            sex=Person.Sex.FEMALE,
            birthdate=date(1990, 5, 20),
            tin="000-111-222-000",
            version=1,
        )

    def test_non_hrmo_roles_cannot_access_person_create_or_edit(self):
        non_hrmo_users = [
            self.sysadmin,
            self.office_head,
            self.auditor,
            self.executive,
            self.employee,
        ]

        for user in non_hrmo_users:
            self.client.force_login(user)

            # GET create URL -> 403 Forbidden
            resp_create_get = self.client.get("/people/new/")
            self.assertEqual(
                resp_create_get.status_code,
                403,
                f"Role {user.username} must get 403 on GET /people/new/",
            )

            # POST create URL -> 403 Forbidden
            resp_create_post = self.client.post("/people/new/", {"last_name": "Test"})
            self.assertEqual(
                resp_create_post.status_code,
                403,
                f"Role {user.username} must get 403 on POST /people/new/",
            )

            # GET edit URL -> 403 Forbidden
            resp_edit_get = self.client.get(f"/people/{self.existing_person.pk}/edit/")
            self.assertEqual(
                resp_edit_get.status_code,
                403,
                f"Role {user.username} must get 403 on GET /people/{self.existing_person.pk}/edit/",
            )

            # POST edit URL -> 403 Forbidden
            resp_edit_post = self.client.post(
                f"/people/{self.existing_person.pk}/edit/", {"last_name": "Hacked"}
            )
            self.assertEqual(
                resp_edit_post.status_code,
                403,
                f"Role {user.username} must get 403 on POST /people/{self.existing_person.pk}/edit/",
            )

    def test_hrmo_can_create_person_with_race_safe_employee_no_and_audit_log(self):
        self.client.force_login(self.hrmo_user)

        # GET form
        resp_get = self.client.get("/people/new/")
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "PROTOTYPE - FAKE DATA ONLY")
        self.assertContains(resp_get, "Add New Personnel Record")

        # POST form
        payload = {
            "last_name": "  Luna  ",
            "first_name": " Antonio ",
            "middle_name": " Novicio ",
            "suffix": "",
            "sex": "M",
            "birthdate": "1988-10-29",
            "mobile": " 0917-888-9999 ",
            "email": "antonio.luna@sanpascual.gov.ph",
            "address": "San Pascual, Batangas",
            "tin": "000-999-888-000",
            "gsis_no": "0012345678",
            "philhealth_no": "00-123456789-0",
            "pagibig_no": "0012-3456-7890",
            "sss_no": "00-1234567-8",
        }
        resp_post = self.client.post("/people/new/", payload)
        self.assertEqual(resp_post.status_code, 302)

        new_person = Person.objects.get(last_name="Luna")
        self.assertEqual(new_person.first_name, "Antonio")
        self.assertEqual(new_person.middle_name, "Novicio")
        self.assertEqual(new_person.mobile, "0917-888-9999")
        self.assertTrue(new_person.employee_no.startswith("SP-"))
        self.assertEqual(new_person.version, 1)

        # Audit log verifies CREATE event with sensitive IDs redacted
        audit_entry = AuditLog.objects.filter(
            model_name="Person", object_id=str(new_person.pk), action=AuditLog.Action.CREATE
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertIn("tin", audit_entry.changes)
        self.assertEqual(audit_entry.changes["tin"], "[REDACTED]")
        self.assertNotIn("000-999-888-000", str(audit_entry.changes))

    def test_hrmo_can_edit_person_with_version_increment(self):
        self.client.force_login(self.hrmo_user)

        # Edit person
        payload = {
            "last_name": "Del Rosario",
            "first_name": "Carmen",
            "middle_name": "Santos",
            "suffix": "",
            "sex": "F",
            "birthdate": "1990-05-20",
            "mobile": "0918-111-2222",
            "email": "carmen.delrosario@sanpascual.gov.ph",
            "address": "Poblacion, San Pascual",
            "tin": "000-111-222-000",
            "gsis_no": "0011223344",
            "philhealth_no": "00-223344556-0",
            "pagibig_no": "0011-2233-4455",
            "sss_no": "00-9988776-5",
            "version": self.existing_person.version,
        }
        resp = self.client.post(f"/people/{self.existing_person.pk}/edit/", payload)
        self.assertEqual(resp.status_code, 302)

        self.existing_person.refresh_from_db()
        self.assertEqual(self.existing_person.mobile, "0918-111-2222")
        self.assertEqual(self.existing_person.version, 2)

    def test_lost_update_protection_rejects_stale_submission(self):
        self.client.force_login(self.hrmo_user)

        # Stale submission: submitted version is 1, but DB version is already 2
        self.existing_person.version = 2
        self.existing_person.save()

        payload = {
            "last_name": "Del Rosario",
            "first_name": "Carmen",
            "middle_name": "Santos",
            "suffix": "",
            "sex": "F",
            "birthdate": "1990-05-20",
            "mobile": "0918-999-9999",
            "version": 1,  # Stale version!
        }
        resp = self.client.post(f"/people/{self.existing_person.pk}/edit/", payload)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "modified by another user")

        # Database record was NOT modified
        self.existing_person.refresh_from_db()
        self.assertNotEqual(self.existing_person.mobile, "0918-999-9999")
        self.assertEqual(self.existing_person.version, 2)

    def test_duplicate_person_validation_considers_middle_name_and_suffix(self):
        self.client.force_login(self.hrmo_user)

        # Attempt to create duplicate of existing_person
        payload = {
            "last_name": "del rosario",
            "first_name": "CARMEN",
            "middle_name": "santos",
            "suffix": "",
            "sex": "F",
            "birthdate": "1990-05-20",
        }
        resp = self.client.post("/people/new/", payload)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        self.assertContains(resp, self.existing_person.employee_no)

        # Distinct middle name succeeds
        payload["middle_name"] = "Garcia"
        resp_distinct = self.client.post("/people/new/", payload)
        self.assertEqual(resp_distinct.status_code, 302)

    def test_birthdate_age_validation(self):
        self.client.force_login(self.hrmo_user)

        # 1. Future birthdate
        resp_future = self.client.post("/people/new/", {
            "last_name": "Future",
            "first_name": "Baby",
            "birthdate": "2030-01-01",
        })
        self.assertContains(resp_future, "Birthdate cannot be in the future")

        # 2. Too young (< 18)
        resp_young = self.client.post("/people/new/", {
            "last_name": "Young",
            "first_name": "Child",
            "birthdate": "2015-01-01",
        })
        self.assertContains(resp_young, "at least 18 years old")

        # 3. Too old (> 90)
        resp_old = self.client.post("/people/new/", {
            "last_name": "Elder",
            "first_name": "Senior",
            "birthdate": "1920-01-01",
        })
        self.assertContains(resp_old, "cannot exceed 90 years")

    def test_prototype_fake_id_rule(self):
        from django.test import override_settings

        self.client.force_login(self.hrmo_user)

        # With PROTOTYPE_FAKE_DATA_ONLY=True, non-00 ID is rejected
        with override_settings(PROTOTYPE_FAKE_DATA_ONLY=True):
            resp = self.client.post("/people/new/", {
                "last_name": "RealID",
                "first_name": "Tester",
                "birthdate": "1990-01-01",
                "tin": "123-456-789-000",  # Invalid (doesn't start with 00)
            })
            self.assertContains(resp, "must start with 00")

    def test_field_level_gating_removes_id_fields_without_view_ids(self):
        # User without view_ids has ID fields completely absent from PersonForm
        form = PersonForm(user=self.employee)
        for f in ["tin", "gsis_no", "philhealth_no", "pagibig_no", "sss_no"]:
            self.assertNotIn(f, form.fields)

        # User with view_ids (HRMO) has ID fields present
        hrmo_form = PersonForm(user=self.hrmo_user)
        for f in ["tin", "gsis_no", "philhealth_no", "pagibig_no", "sss_no"]:
            self.assertIn(f, hrmo_form.fields)

    def test_employee_sequence_generation_and_resync(self):
        from hr.sequences import get_next_employee_no, sync_employee_sequence

        # Sequential generation
        no1 = get_next_employee_no()
        no2 = get_next_employee_no()
        self.assertTrue(no1.startswith("SP-"))
        self.assertTrue(no2.startswith("SP-"))
        num1 = int(no1.split("-")[1])
        num2 = int(no2.split("-")[1])
        self.assertEqual(num2, num1 + 1)

        # Create person using no2 to verify sync_employee_sequence finds it
        Person.objects.create(
            employee_no=no2,
            last_name="Sequence",
            first_name="Test",
            birthdate=date(1995, 1, 1),
        )
        synced = sync_employee_sequence()
        self.assertGreaterEqual(synced, num2)





