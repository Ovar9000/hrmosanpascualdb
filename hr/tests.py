from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
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
    UserProfile,
)
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




