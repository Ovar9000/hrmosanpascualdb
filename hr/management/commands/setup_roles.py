"""Sets up the standard Django auth Groups and assigns role permissions.

Idempotent: safe to run repeatedly.
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from hr.models import Delegate, Document, Engagement, LeaveRequest, Office, Person
from hr.permissions import (
    ROLE_AUDITOR,
    ROLE_EXECUTIVE,
    ROLE_HRMO,
    ROLE_OFFICE_HEAD,
    ROLE_OFFICE_STAFF,
    ROLE_SYSADMIN,
)


class Command(BaseCommand):
    help = "Initializes default roles (Django Groups) and assigns explicit permissions."

    def handle(self, *args, **options):
        self.stdout.write("Configuring system roles and permissions...")

        # Obtain content types
        person_ct = ContentType.objects.get_for_model(Person)
        eng_ct = ContentType.objects.get_for_model(Engagement)
        doc_ct = ContentType.objects.get_for_model(Document)
        leave_ct = ContentType.objects.get_for_model(LeaveRequest)
        delegate_ct = ContentType.objects.get_for_model(Delegate)
        office_ct = ContentType.objects.get_for_model(Office)

        # 1. System Admin (IT)
        # Manages accounts and groups in /admin/. Has ZERO HR permissions.
        sysadmin_group, _ = Group.objects.get_or_create(name=ROLE_SYSADMIN)
        sysadmin_perms = Permission.objects.filter(
            content_type__app_label="auth",
            codename__in=["add_user", "change_user", "view_user", "view_group"],
        )
        sysadmin_group.permissions.set(sysadmin_perms)

        # 2. HRMO Staff
        # Full read/write for people, appointments, documents, leave credits. Can view pay and IDs.
        hrmo_group, _ = Group.objects.get_or_create(name=ROLE_HRMO)
        hrmo_perms = Permission.objects.filter(
            content_type__in=[person_ct, eng_ct, doc_ct, leave_ct, office_ct, delegate_ct]
        )
        hrmo_group.permissions.set(hrmo_perms)

        # 3. Office Head
        # Scoped to own office. Recommends/approves leave. No pay or IDs.
        office_head_group, _ = Group.objects.get_or_create(name=ROLE_OFFICE_HEAD)
        office_head_perms = Permission.objects.filter(
            content_type=person_ct, codename__in=["view_hr_records", "view_person"]
        ) | Permission.objects.filter(
            content_type=leave_ct, codename__in=["approve_leave", "view_leaverequest"]
        ) | Permission.objects.filter(
            content_type=office_ct, codename="view_office"
        ) | Permission.objects.filter(
            content_type=eng_ct, codename="view_engagement"
        ) | Permission.objects.filter(
            content_type=doc_ct, codename="view_document"
        )
        office_head_group.permissions.set(office_head_perms)

        # 4. Office Staff
        # Scoped to own office. Read-only (names, positions). No IDs, docs, or pay.
        office_staff_group, _ = Group.objects.get_or_create(name=ROLE_OFFICE_STAFF)
        office_staff_perms = Permission.objects.filter(
            content_type=person_ct, codename__in=["view_hr_records", "view_person"]
        ) | Permission.objects.filter(
            content_type=office_ct, codename="view_office"
        ) | Permission.objects.filter(
            content_type=eng_ct, codename="view_engagement"
        )
        office_staff_group.permissions.set(office_staff_perms)

        # 5. Executive (Mayor, Administrator)
        # Reads across offices. Approves leave. No pay or IDs (data minimization).
        exec_group, _ = Group.objects.get_or_create(name=ROLE_EXECUTIVE)
        exec_perms = Permission.objects.filter(
            content_type=person_ct, codename__in=["view_hr_records", "view_person"]
        ) | Permission.objects.filter(
            content_type=leave_ct, codename__in=["approve_leave", "view_leaverequest"]
        ) | Permission.objects.filter(
            content_type=office_ct, codename="view_office"
        ) | Permission.objects.filter(
            content_type=eng_ct, codename="view_engagement"
        ) | Permission.objects.filter(
            content_type=doc_ct, codename="view_document"
        )
        exec_group.permissions.set(exec_perms)

        # 6. Auditor
        # Read-only across all offices, including pay, IDs, documents, and audit logs.
        auditor_group, _ = Group.objects.get_or_create(name=ROLE_AUDITOR)
        auditor_perms = Permission.objects.filter(
            content_type__in=[person_ct, eng_ct, doc_ct, office_ct, leave_ct],
            codename__in=[
                "view_hr_records", "view_person", "view_pay", "view_engagement",
                "view_ids", "view_documents", "view_document", "view_office",
                "view_leaverequest",
            ],
        )
        auditor_group.permissions.set(auditor_perms)


        self.stdout.write(self.style.SUCCESS("System roles configured successfully."))
