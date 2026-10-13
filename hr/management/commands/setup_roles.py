"""Sets up the standard Django auth Groups and assigns role permissions.

Idempotent: safe to run repeatedly. Runs entirely inside disable_audit(): this is
deployment-time provisioning by the DB owner, not a user action. Group membership
changes made later in /admin/ are still audited (DECISIONS R3).
"""

from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from hr.audit import disable_audit
from hr.models import AuditLog, Delegate, Document, Engagement, LeaveRequest, Office, Person, Project
from hr.permissions import (
    ROLE_AUDITOR,
    ROLE_EXECUTIVE,
    ROLE_HRMO,
    ROLE_OFFICE_HEAD,
    ROLE_OFFICE_STAFF,
    ROLE_PROJECT_MAINTAINER,
    ROLE_SYSADMIN,
)


class Command(BaseCommand):
    help = "Initializes default roles (Django Groups) and assigns explicit permissions."

    def handle(self, *args, **options):
        with disable_audit():
            self.stdout.write("Configuring system roles and permissions...")

            # Obtain content types
            person_ct = ContentType.objects.get_for_model(Person)
            eng_ct = ContentType.objects.get_for_model(Engagement)
            doc_ct = ContentType.objects.get_for_model(Document)
            leave_ct = ContentType.objects.get_for_model(LeaveRequest)
            delegate_ct = ContentType.objects.get_for_model(Delegate)
            office_ct = ContentType.objects.get_for_model(Office)
            audit_ct = ContentType.objects.get_for_model(AuditLog)
            project_ct = ContentType.objects.get_for_model(Project)
            view_project = Permission.objects.filter(content_type=project_ct, codename="view_project")

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
            ) | view_project
            # Viewing projects only: creating and editing them needs the Project Maintainer
            # group, so hiring and funding a project can sit with different offices (P1).
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
            ) | view_project
            exec_group.permissions.set(exec_perms)

            # 6. Auditor
            # Read-only across all offices, including pay, IDs, documents, and audit logs.
            auditor_group, _ = Group.objects.get_or_create(name=ROLE_AUDITOR)
            auditor_perms = Permission.objects.filter(
                content_type__in=[person_ct, eng_ct, doc_ct, office_ct, leave_ct, audit_ct, project_ct],
                codename__in=[
                    "view_hr_records", "view_person", "view_pay", "view_engagement",
                    "view_ids", "view_documents", "view_document", "view_office",
                    "view_leaverequest", "view_audit_log", "view_project",
                ],
            )
            auditor_group.permissions.set(auditor_perms)

            # 7. Project Maintainer
            # Creates and edits projects (dates, status, funding reference). No personnel access:
            # sees headcounts only. Given to HRMO, Planning or Budget staff by LGU decision (P1).
            maintainer_group, _ = Group.objects.get_or_create(name=ROLE_PROJECT_MAINTAINER)
            maintainer_perms = Permission.objects.filter(
                content_type=project_ct,
                codename__in=["manage_projects", "add_project", "change_project", "view_project"],
            ) | Permission.objects.filter(content_type=office_ct, codename="view_office")
            maintainer_group.permissions.set(maintainer_perms)

            self.stdout.write(self.style.SUCCESS("System roles configured successfully."))
