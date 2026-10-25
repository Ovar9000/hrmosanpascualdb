"""Role definitions, query scoping, and field-level permissions for San Pascual HR.

Roles (Django Groups):
- System Admin (IT): Manages user accounts and settings in /admin/. Zero access to HR records.
- HRMO Staff: Full read/write for all personnel and engagements across all offices. Can view pay and IDs.
- Office Head: Scoped to their own office (active engagements only). Can recommend/approve leave. No pay or IDs.
- Office Staff: Scoped to their own office. Read-only (names, positions, contacts). No IDs, docs, or pay.
- Executive (Mayor, Administrator): Scoped across all offices. Can approve leave. No pay or IDs (data minimization).
- Auditor: Read-only access across all offices, including pay, IDs, documents, and audit logs.
- Employee: Self-service only (their own record and leave).
- Executive Delegate: Dynamic role granted via time-bound Delegate model on behalf of an Executive.
- Project Maintainer: Creates and edits LGU projects (dates, status, funding reference). Sees project
  headcounts, never personnel records. Assigned to HRMO, Planning or Budget staff by LGU decision (P1).
"""

from django.db.models import Q
from django.utils import timezone
from .models import Delegate, Engagement, Office, Person

# Group name constants
ROLE_SYSADMIN = "System Admin"
ROLE_HRMO = "HRMO Staff"
ROLE_OFFICE_HEAD = "Office Head"
ROLE_OFFICE_STAFF = "Office Staff"
ROLE_EXECUTIVE = "Executive"
ROLE_AUDITOR = "Auditor"
ROLE_PROJECT_MAINTAINER = "Project Maintainer"

# Every role that grants access to HR records. Only the emergency superuser may assign
# these or edit their members, so System Admin cannot escalate into HR data (DECISIONS R3).
PROTECTED_ROLES = [
    ROLE_HRMO,
    ROLE_AUDITOR,
    ROLE_EXECUTIVE,
    ROLE_OFFICE_HEAD,
    ROLE_OFFICE_STAFF,
    ROLE_PROJECT_MAINTAINER,  # controls which projects HRMO can hire into
    "Executive Delegate",
    "Delegate",
]


def is_in_group(user, group_name):
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name=group_name).exists()


def is_sysadmin(user):
    return is_in_group(user, ROLE_SYSADMIN)


def is_hrmo(user):
    return is_in_group(user, ROLE_HRMO)


def is_auditor(user):
    return is_in_group(user, ROLE_AUDITOR)


def is_office_head(user):
    return is_in_group(user, ROLE_OFFICE_HEAD)


def is_office_staff(user):
    return is_in_group(user, ROLE_OFFICE_STAFF)


def is_active_delegate(user, target_date=None):
    """Checks whether user has an active, valid delegation from an Executive (DECISIONS R7)."""
    if not user or not user.is_authenticated:
        return False
    if target_date is None:
        target_date = timezone.localdate()
    return Delegate.objects.filter(
        delegate=user,
        valid_from__lte=target_date,
        valid_to__gte=target_date,
    ).exists()


def is_executive(user):
    return is_in_group(user, ROLE_EXECUTIVE) or is_active_delegate(user)


def has_all_office_scope(user):
    """Roles that see personnel across every office: superuser, HRMO, Auditor, Executive, Delegate."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or is_hrmo(user) or is_auditor(user) or is_executive(user)


def is_self(user, person):
    """True when the logged-in user's profile is linked to this Person (employee self-service)."""
    return bool(person) and hasattr(user, "profile") and user.profile.person_id == person.id


def get_user_office(user):
    """Resolves the user's assigned Office, either from UserProfile.office or active engagement."""
    if not user or not user.is_authenticated:
        return None
    if hasattr(user, "profile"):
        if user.profile.office:
            return user.profile.office
        if user.profile.person:
            active_eng = user.profile.person.engagements.filter(status=Engagement.Status.ACTIVE).first()
            if active_eng:
                return active_eng.office
    return None


# --- Field-level & Action Permissions -----------------------------------------


def can_view_pay(user):
    """Pay rate, salary grade and step visible ONLY to HRMO Staff and Auditor (DECISIONS R6).

    Salary grade + step gives the exact salary from the public SSL table, so it is pay data.

    Superuser is allowed for emergency administration only.
    Hidden from Office Heads, Office Staff, Executives, Delegates, and Employees.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm("hr.view_pay") or is_hrmo(user) or is_auditor(user)


def can_view_ids(user, person=None):
    """Government IDs (TIN, GSIS, PhilHealth, Pag-IBIG, SSS) visible to HRMO, Auditor, or self."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.has_perm("hr.view_ids") or is_hrmo(user) or is_auditor(user):
        return True
    return is_self(user, person)


def can_view_document_content(user, document=None, person=None):
    """Document file download / content inspection visible to HRMO, Auditor, or self.

    Office heads see document existence, type, status, and physical location,
    but not sensitive electronic scans (DECISIONS R4).
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.has_perm("hr.view_documents") or is_hrmo(user) or is_auditor(user):
        return True
    if document:
        person = document.person or (document.engagement.person if document.engagement_id else None)
    return is_self(user, person)


def can_view_personal_details(user, person=None):
    """Birthdate, sex and home address: same audience as government IDs (HRMO, Auditor, self).

    Office Heads, Office Staff, Executives and Delegates do not see them (DECISIONS R4).
    """
    return can_view_ids(user, person)


def can_view_contacts(user, person=None):
    """Mobile and email: personal-details roles plus Office Head / Office Staff (R4 "contacts").

    Office roles only ever see people in their own office (get_visible_people). Executives and
    Delegates see name, position, office and type only.
    """
    if can_view_personal_details(user, person):
        return True
    return is_office_head(user) or is_office_staff(user)


def can_view_document_list(user, person=None):
    """Which documents exist (type, status, original on file, archive location).

    Document content roles plus Office Heads. File names and notes count as content and stay
    with can_view_document_content. Office Staff, Executives and Delegates see no documents (R4).
    """
    return can_view_document_content(user, person=person) or is_office_head(user)


def can_view_hr_notes(user):
    """Internal HR notes on engagements (e.g. why an appointment ended): HRMO and Auditor."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm("hr.manage_hr_records") or is_hrmo(user) or is_auditor(user)


def can_approve_leave(user):
    """Can act on leave requests: Office Heads, Executives, and active Delegates."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm("hr.approve_leave") or is_office_head(user) or is_executive(user) or is_hrmo(user)


def can_manage_hr_records(user):
    """Create or edit personnel and appointments."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm("hr.manage_hr_records") or is_hrmo(user)


def can_manage_projects(user):
    """Create and edit LGU projects: Project Maintainer role only (DECISIONS P1).

    Deliberately separate from can_manage_hr_records, so the office that defines and funds a
    project need not be the office that hires into it. HRMO gets it only by joining the group.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm("hr.manage_projects") or is_in_group(user, ROLE_PROJECT_MAINTAINER)


def can_view_projects(user):
    """Project list and details (no personnel): maintainers plus all-office HR roles."""
    return can_manage_projects(user) or has_all_office_scope(user)


def can_view_project_roster(user):
    """Names of the people hired on a project: all-office HR roles. Project Maintainers see counts only."""
    return has_all_office_scope(user)


# --- Queryset Scoping ---------------------------------------------------------


def get_visible_people(user):
    """Returns Person queryset scoped to user role.

    - Superuser, HRMO, Executive, Auditor, Active Delegate: All people.
    - Office Head / Office Staff: People with an ACTIVE engagement in their office.
    - Employee: Only their own record.
    - System Admin (IT): None (zero HR record access).
    """
    if not user or not user.is_authenticated:
        return Person.objects.none()

    if has_all_office_scope(user):
        return Person.objects.all()

    # Office Head or Office Staff: Scoped to current active office engagements
    if is_office_head(user) or is_office_staff(user):
        office = get_user_office(user)
        if office:
            return Person.objects.filter(
                engagements__office=office,
                engagements__status=Engagement.Status.ACTIVE,
            ).distinct()
        return Person.objects.none()

    # Employee self-service
    if hasattr(user, "profile") and user.profile.person:
        return Person.objects.filter(pk=user.profile.person.pk)

    # System Admin / Unassigned: Zero access
    return Person.objects.none()


def get_visible_offices(user):
    """Returns Office queryset scoped to user role."""
    if not user or not user.is_authenticated:
        return Office.objects.none()

    if has_all_office_scope(user):
        return Office.objects.all()

    if is_office_head(user) or is_office_staff(user):
        office = get_user_office(user)
        if office:
            return Office.objects.filter(pk=office.pk)
        return Office.objects.none()

    return Office.objects.none()



# --- Org charts (DECISIONS O3) ------------------------------------------------


def can_view_office_tree(user):
    """The municipal office tree (office names and head titles): anyone with HR access or an office.

    Head names and staff counts on it follow can_view_office_chart per office.
    """
    if not user or not user.is_authenticated or (is_sysadmin(user) and not user.is_superuser):
        return False
    return has_all_office_scope(user) or get_user_office(user) is not None


def can_view_office_chart(user, office):
    """An office's staff chart: offices the role can already see, plus the user's own office.

    The second part is employee self-service, so a new hire can see their head and coworkers
    (names and positions only, see can_view_chart_types).
    """
    if not user or not user.is_authenticated or office is None:
        return False
    if is_sysadmin(user) and not user.is_superuser:
        return False
    if get_visible_offices(user).filter(pk=office.pk).exists():
        return True
    own = get_user_office(user)
    return own is not None and own.pk == office.pk


def can_view_chart_types(user):
    """Appointment type on chart boxes: the roles that already see it on office pages (R4)."""
    return has_all_office_scope(user) or is_office_head(user) or is_office_staff(user)


# --- Salary actions (DECISIONS SA4) -------------------------------------------


def can_manage_salary_actions(user):
    """Review and issue step increments and loyalty awards: HRMO."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm("hr.manage_salary_actions") or is_hrmo(user)


def can_view_salary_actions(user):
    """See the tracker and cases: HRMO and Auditor. Steps are pay data (R6), so no office roles."""
    if not user or not user.is_authenticated:
        return False
    return can_manage_salary_actions(user) or user.has_perm("hr.view_salary_actions") or is_auditor(user)


def can_view_trackers(user):
    """Contract renewals, retirement and year-end bonus lists (DECISIONS RM4): they use birthdates
    and pay eligibility, so the same audience as salary actions: HRMO and Auditor."""
    return can_view_salary_actions(user)


# --- Leave, Plantilla report, vacancies and memos (DECISIONS L1-L9, RP1, V1-V5, M1-M4) --------


def _no_hr_access(user):
    return not user or not user.is_authenticated or (is_sysadmin(user) and not user.is_superuser)


def can_view_leave_of(user, person):
    """A person's leave card and applications (R4 leave balances): HRMO, Auditor and self, plus
    leave approvers for the people they can already see."""
    if _no_hr_access(user):
        return False
    if can_view_personal_details(user, person):
        return True
    return can_approve_leave(user) and get_visible_people(user).filter(pk=person.pk).exists()


def can_view_leave_register(user):
    """Every application, the balances board and the ledger tools: HRMO (edits) and Auditor (reads)."""
    if _no_hr_access(user):
        return False
    return user.is_superuser or is_hrmo(user) or is_auditor(user)


def can_view_plantilla_report(user):
    """The Plantilla of Personnel shows salaries and birthdates (R4, R6): HRMO and Auditor."""
    return can_view_pay(user) and can_view_leave_register(user)


def can_view_vacancies(user):
    """Published vacancies are public notices (posted on the bulletin boards): every signed-in user."""
    return not _no_hr_access(user)


def can_view_applicants(user):
    """Applicant names and documents: HRMO, Auditor and the Executive (the HRMPSB chair)."""
    if _no_hr_access(user):
        return False
    return user.is_superuser or is_hrmo(user) or is_auditor(user) or is_executive(user)


def memo_is_for(memo, person):
    """Whether a memo is addressed to this person (everyone in service, one office, or chosen)."""
    if person is None:
        return False
    if memo.audience == memo.Audience.CHOSEN:
        return memo.members.filter(pk=person.pk).exists()
    active = person.engagements.filter(status=Engagement.Status.ACTIVE)
    if memo.audience == memo.Audience.OFFICE:
        active = active.filter(office_id=memo.office_id)
    return active.exists()


def can_view_memo(user, memo):
    """Published memos: their addressees and all-office roles. Drafts: HRMO only."""
    if _no_hr_access(user):
        return False
    if not memo.is_published:
        return can_manage_hr_records(user)
    if has_all_office_scope(user):
        return True
    if memo.audience == memo.Audience.OFFICE and memo.office_id:
        own = get_user_office(user)
        if own and own.pk == memo.office_id:
            return True
    person = getattr(getattr(user, "profile", None), "person", None)
    return memo_is_for(memo, person)


def memo_ack_scope(user):
    """Whose acknowledgments the user may see: None for everyone (all-office roles), an office for
    an Office Head (own office only), or False."""
    if _no_hr_access(user):
        return False
    if has_all_office_scope(user):
        return None
    if is_office_head(user):
        return get_user_office(user) or False
    return False


# --- Service records and certificates of employment (DECISIONS CR5) --------------------------


def can_view_certificate_requests(user):
    """Every request and the printed documents (they show salaries, R6): HRMO and Auditor."""
    if _no_hr_access(user):
        return False
    return user.is_superuser or is_hrmo(user) or is_auditor(user)


def can_manage_certificate_requests(user):
    """Record requests for walk-ins and letters, prepare, release and decline: HRMO."""
    return can_manage_hr_records(user) and can_view_certificate_requests(user)


# --- Personal Data Sheet (DECISIONS PD5) ------------------------------------------------------


def can_view_pds(user, person):
    """The PDS holds family, health, offense and disability answers (sensitive personal information
    under RA 10173): the person, HRMO and Auditor only."""
    if not user or not user.is_authenticated or person is None:
        return False
    if is_self(user, person):
        return True
    return not _no_hr_access(user) and (user.is_superuser or is_hrmo(user) or is_auditor(user))


def can_edit_pds(user, person):
    """The person fills in their own; HRMO encodes paper PDS for people without an account."""
    if not user or not user.is_authenticated or person is None:
        return False
    return is_self(user, person) or can_manage_hr_records(user)


def can_verify_pds(user):
    return can_manage_hr_records(user) and not _no_hr_access(user)


def can_view_pds_queue(user):
    return not _no_hr_access(user) and (user.is_superuser or is_hrmo(user) or is_auditor(user))


# --- Performance ratings and SALN filing (DECISIONS SP5, SN3) ---------------------------------


def can_view_ratings(user, person):
    """A person's SPMS ratings: the person, HRMO, Auditor, the Executive (who approves the office
    ratings) and the Office Head of their office, who rated them."""
    if not user or not user.is_authenticated or person is None:
        return False
    if is_self(user, person):
        return True
    if _no_hr_access(user):
        return False
    if user.is_superuser or is_hrmo(user) or is_auditor(user) or is_executive(user):
        return True
    office = get_user_office(user)
    return bool(is_office_head(user) and office and person.engagements.filter(
        office=office, status=Engagement.Status.ACTIVE).exists())


def saln_scope(user):
    """Whose SALN filing the user may see: None for everyone (HRMO, Auditor), an office for an
    Office Head (own office only), or False. Only whether it was filed, never the SALN itself."""
    if _no_hr_access(user):
        return False
    if user.is_superuser or is_hrmo(user) or is_auditor(user):
        return None
    if is_office_head(user):
        return get_user_office(user) or False
    return False
