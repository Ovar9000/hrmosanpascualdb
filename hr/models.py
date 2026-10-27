"""HR data model (first draft).

Kept deliberately flat and easy to change after the HRMO survey (see DECISIONS.md):
option lists are TextChoices in this file; most business rules are DB CHECK
constraints, which Django also validates in forms and the admin.
"""

import hashlib
import json
import os
import uuid
from datetime import date
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils import timezone

# --- Offices -----------------------------------------------------------------


class Office(models.Model):
    class Branch(models.TextChoices):
        EXECUTIVE = "executive", "Executive"
        LEGISLATIVE = "legislative", "Legislative"
        OTHER = "other", "Other"

    class Type(models.TextChoices):
        DEPARTMENT = "department", "Department"
        SPECIAL = "special", "Special office"
        INSTITUTION = "institution", "Institution"

    name = models.CharField(max_length=200, unique=True)
    acronym = models.CharField(max_length=20, blank=True)
    branch = models.CharField(max_length=20, choices=Branch.choices)
    type = models.CharField(max_length=20, choices=Type.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    head_title = models.CharField(max_length=150, blank=True)
    # Office tree (DECISIONS O6): drawn beside its parent's head, not in the row of divisions below.
    coterminous = models.BooleanField(
        default=False,
        help_text="Coterminous with the head of the parent office: drawn beside that head on the office tree.",
    )
    location = models.CharField(max_length=200, blank=True)
    contact = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class OrgUnit(models.Model):
    """A division or section inside an office, or a body attached to its head, for the
    office's structure chart (DECISIONS O7)."""

    class Kind(models.TextChoices):
        DIVISION = "division", "Division"
        SECTION = "section", "Section"
        BODY = "body", "Local special bodies"

    office = models.ForeignKey(Office, on_delete=models.CASCADE, related_name="units")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children",
        help_text="Empty: directly under the head of the office.",
    )
    name = models.CharField(max_length=200)
    acronym = models.CharField(max_length=20, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.DIVISION)
    beside_head = models.BooleanField(default=False, help_text="Drawn beside the head, not below it.")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["office", "order", "name"]

    def __str__(self):
        return self.name


class StructurePosition(models.Model):
    """A position on an office's approved structure (elected, existing filled-up or proposed for
    creation), as drawn on its structure chart. Not an appointment: who holds it lives in
    Engagement (DECISIONS O7)."""

    class Status(models.TextChoices):
        ELECTED = "elected", "Elected position"
        FILLED = "filled", "Existing filled-up position"
        PROPOSED = "proposed", "Proposed for creation"

    office = models.ForeignKey(Office, on_delete=models.CASCADE, related_name="structure_positions")
    unit = models.ForeignKey(
        OrgUnit, null=True, blank=True, on_delete=models.CASCADE, related_name="positions",
        help_text="Empty: the head of the office, or beside the head.",
    )
    title = models.CharField(max_length=200)
    salary_grade = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    beside_head = models.BooleanField(default=False, help_text="Drawn beside the head, not below it.")
    coterminous = models.BooleanField(default=False, help_text="Drawn with a dashed line.")
    reports_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports",
        help_text="The position it is drawn under, in the same division or section. "
        "Empty: directly under its division or section.",
    )
    joins_row = models.BooleanField(
        default=False, help_text="Drawn under the row of positions above it, joined to the bottom of each.",
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["office", "order", "title"]

    def clean(self):
        if self.reports_to_id and (self.reports_to_id == self.pk or self.reports_to.unit_id != self.unit_id):
            raise ValidationError({"reports_to": "Choose another position in the same division or section."})

    def __str__(self):
        return f"{self.title} (SG {self.salary_grade})" if self.salary_grade else self.title


# --- People ------------------------------------------------------------------

# Prototype guard (DECISIONS D1): government ID fields only accept obviously fake
# values starting with "00". PROTOTYPE_FAKE_DATA_ONLY does not affect these DB
# constraints; removing them needs a deliberate migration (Go-Live Checklist item 1).
FAKE_ID_FIELDS = ["tin", "gsis_no", "philhealth_no", "pagibig_no", "sss_no"]
FAKE_ID_REGEX = r"^00[0-9-]*$"


class Person(models.Model):
    """One row per human, ever. Appointments/contracts live in Engagement."""

    class Sex(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"

    employee_no = models.CharField("Employee no.", max_length=20, unique=True)
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    suffix = models.CharField(max_length=10, blank=True, help_text="Jr., Sr., III")
    sex = models.CharField(max_length=1, choices=Sex.choices, blank=True)
    birthdate = models.DateField()
    mobile = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)

    tin = models.CharField("TIN", max_length=20, blank=True)
    gsis_no = models.CharField("GSIS BP no.", max_length=20, blank=True)
    philhealth_no = models.CharField("PhilHealth no.", max_length=20, blank=True)
    pagibig_no = models.CharField("Pag-IBIG MID no.", max_length=20, blank=True)
    sss_no = models.CharField("SSS no.", max_length=20, blank=True)

    # Concurrency control & audit timestamp (DECISIONS S2)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name_plural = "people"
        indexes = [
            models.Index(fields=["last_name", "first_name"], name="person_name_idx"),
            models.Index(fields=["employee_no"], name="person_emp_no_idx"),
        ]
        permissions = [
            ("view_ids", "Can view government identification numbers"),
            ("view_hr_records", "Can view personnel directory and records"),
            ("manage_hr_records", "Can manage personnel records"),
        ]
        constraints = [
            # DECISIONS S5: duplicate person considers last name, first name, middle name, suffix, and birthdate.
            models.UniqueConstraint(
                Lower("last_name"), Lower("first_name"), Lower("middle_name"), Lower("suffix"), "birthdate",
                name="person_unique_name_birthdate",
                violation_error_message="A person with this name, middle name, suffix, and birthdate already exists.",
            ),
        ] + [
            models.CheckConstraint(
                condition=Q(**{f: ""}) | Q(**{f"{f}__regex": FAKE_ID_REGEX}),
                name=f"person_{f}_fake_only",
                violation_error_message="Prototype accepts fake ID numbers only (must start with 00).",
            )
            for f in FAKE_ID_FIELDS
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        middle = f" {self.middle_name[0]}." if self.middle_name else ""
        suffix = f" {self.suffix}" if self.suffix else ""
        return f"{self.last_name}, {self.first_name}{middle}{suffix}"

    def earns_leave_credits(self):
        """Only active engagements with leave_eligible=True earn leave credits (DECISIONS L3)."""
        return self.engagements.filter(
            status=Engagement.Status.ACTIVE,
            leave_eligible=True,
        ).exists()


class EmployeeSequence(models.Model):
    """Monotonically increasing sequence generator for race-safe employee numbers (DECISIONS S1)."""

    name = models.CharField(max_length=50, unique=True)
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "hr_employeesequence"

    def __str__(self):
        return f"{self.name}: {self.last_number}"


# --- Projects ----------------------------------------------------------------


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ONGOING = "ongoing", "Ongoing"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    # Statuses HRMO can still hire into (DECISIONS P3).
    HIRING_STATUSES = [Status.PLANNED, Status.ONGOING]

    name = models.CharField(max_length=200)
    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name="projects")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    # Reference to the Budget/Accounting document certifying the project is funded, so
    # contracts are tied to an independent sign-off, not only HR's word (DECISIONS P2).
    funding_reference = models.CharField(
        "Funding reference",
        max_length=100,
        blank=True,
        help_text="Certificate or obligation number from Budget / Accounting.",
    )
    notes = models.TextField(blank=True)

    # Concurrency control & audit timestamp (DECISIONS S2)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "name"]
        permissions = [
            ("manage_projects", "Can create and edit LGU projects"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="project_end_after_start",
                violation_error_message="End date cannot be before start date.",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_open_for_hiring(self):
        """New contracts may link only to planned/ongoing projects with a funding reference (P2, P3)."""
        return self.status in self.HIRING_STATUSES and bool(self.funding_reference)


# --- Engagements -------------------------------------------------------------


class Engagement(models.Model):
    """One row per appointment or contract. Never overwritten: a new appointment is a new row."""

    class Type(models.TextChoices):
        ELECTED = "elected", "Elected"
        PLANTILLA = "plantilla", "Plantilla"
        CASUAL = "casual", "Casual"
        CONTRACTUAL = "contractual", "Contractual"
        JOB_ORDER = "jo", "Job Order"
        CONTRACT_OF_SERVICE = "cos", "Contract of Service"
        PROJECT_BASED = "project", "Project-based"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended (contract/term expired)"
        RESIGNED = "resigned", "Resigned"
        RETIRED = "retired", "Retired"
        TERMINATED = "terminated", "Terminated"

    class RateBasis(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        DAILY = "daily", "Daily"
        LUMP_SUM = "lump_sum", "Lump sum"

    LEAVE_CREDIT_TYPES = [Type.PLANTILLA, Type.CASUAL]
    # Types drawn as boxes on the org chart; JO, COS and project-based are only counted (DECISIONS O1).
    CHART_TYPES = [Type.ELECTED, Type.PLANTILLA, Type.CASUAL, Type.CONTRACTUAL]

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="engagements")
    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name="engagements")
    type = models.CharField(max_length=20, choices=Type.choices)
    position_title = models.CharField(max_length=150)
    # Org chart reporting line: an active chart-type engagement in the same office (DECISIONS O2).
    # Empty means top of the office chart (usually the office head).
    reports_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="direct_reports",
        verbose_name="Reports to",
    )
    # Payroll *input* only (DECISIONS D2). Visible to HRMO and auditors only (R6).
    rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_basis = models.CharField(max_length=20, choices=RateBasis.choices, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    leave_eligible = models.BooleanField(
        "Leave eligible",
        default=False,
        help_text="Eligible to earn leave credits. Default True for Plantilla/Casual, editable by HRMO.",
    )

    class AppointmentStatus(models.TextChoices):
        PERMANENT = "P", "Permanent"
        TEMPORARY = "T", "Temporary"
        COTERMINOUS = "CO", "Coterminous"

    # Plantilla only (DECISIONS D5).
    salary_grade = models.PositiveSmallIntegerField(null=True, blank=True)
    step = models.PositiveSmallIntegerField(null=True, blank=True)
    item_number = models.CharField(max_length=50, blank=True)
    # Status column of the Plantilla of Personnel (DECISIONS RP1).
    appointment_status = models.CharField(
        "Appointment status", max_length=2, choices=AppointmentStatus.choices, blank=True,
    )

    # Project-based only.
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.PROTECT, related_name="engagements"
    )
    notes = models.TextField(blank=True)

    # Concurrency control & audit timestamp (DECISIONS S2)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        permissions = [
            ("view_pay", "Can view compensation rates and salary grades"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="engagement_end_after_start",
                violation_error_message="End date cannot be before start date.",
            ),
            models.CheckConstraint(
                condition=(
                    Q(type="plantilla", salary_grade__isnull=False, step__isnull=False)
                    & ~Q(item_number="")
                )
                | (
                    ~Q(type="plantilla")
                    & Q(salary_grade__isnull=True, step__isnull=True, item_number="")
                ),
                name="engagement_plantilla_fields",
                violation_error_message=(
                    "Salary grade, step and item number are required for Plantilla "
                    "and must be empty for other types."
                ),
            ),
            models.CheckConstraint(
                condition=Q(salary_grade__isnull=True) | Q(salary_grade__range=(1, 33)),
                name="engagement_salary_grade_range",
                violation_error_message="Salary grade must be 1 to 33.",
            ),
            models.CheckConstraint(
                condition=Q(step__isnull=True) | Q(step__range=(1, 8)),
                name="engagement_step_range",
                violation_error_message="Step must be 1 to 8.",
            ),
            models.CheckConstraint(
                condition=Q(type="project", project__isnull=False)
                | (~Q(type="project") & Q(project__isnull=True)),
                name="engagement_project_link",
                violation_error_message="Project-based engagements must link a project; others must not.",
            ),
            models.CheckConstraint(
                condition=Q(rate__isnull=True, rate_basis="")
                | (Q(rate__gte=0) & ~Q(rate_basis="")),
                name="engagement_rate_with_basis",
                violation_error_message="Rate and rate basis go together (rate cannot be negative).",
            ),
        ]

    def __str__(self):
        return f"{self.person} – {self.position_title} ({self.get_type_display()})"

    @classmethod
    def default_leave_eligible(cls, engagement_type):
        """Suggested leave_eligible for a new engagement of this type (DECISIONS L3).

        Only a default for entry screens and imports to apply explicitly; save() never
        overrides the stored value, so HRMO can record Plantilla/Casual without leave.
        """
        return engagement_type in cls.LEAVE_CREDIT_TYPES

    @property
    def on_chart(self):
        return self.type in self.CHART_TYPES


# --- Documents (kept simple until the survey, DECISIONS F1) ------------------


def validate_file_size(f):
    max_size = getattr(settings, "MAX_UPLOAD_SIZE", 5 * 1024 * 1024)
    if f.size > max_size:
        raise ValidationError(f"File too large (maximum allowed size is {max_size // (1024 * 1024)} MB).")


def document_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    rand_name = f"{uuid.uuid4().hex}{ext}"
    date_path = instance.uploaded_at.strftime("%Y/%m") if instance.uploaded_at else date.today().strftime("%Y/%m")
    return f"documents/{date_path}/{rand_name}"


class Document(models.Model):
    class Type(models.TextChoices):
        PDS = "pds", "Personal Data Sheet"
        APPOINTMENT = "appointment", "Appointment / contract"
        SERVICE_RECORD = "service_record", "Service record"
        CLEARANCE = "clearance", "Clearance"
        CERTIFICATE = "certificate", "Certificate"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        APPROVED = "approved", "Approved"
        FILED = "filed", "Filed"

    person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="documents"
    )
    engagement = models.ForeignKey(
        Engagement, null=True, blank=True, on_delete=models.PROTECT, related_name="documents"
    )
    doc_type = models.CharField("Type", max_length=30, choices=Type.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    # Optional: a record may track a paper-only document by its physical location.
    file = models.FileField(
        upload_to=document_upload_path,
        blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"]), validate_file_size],
    )
    display_name = models.CharField(
        max_length=255, blank=True, help_text="Original sanitized filename shown to users"
    )
    # The system tracks paper; it does not replace it. Wet signatures stay (survey Oct 2026).
    original_on_file = models.BooleanField(
        "Original paper copy on file",
        default=True,
        help_text="Whether the wet-signed physical paper document is filed in the records archive.",
    )
    physical_location = models.CharField(
        max_length=100, blank=True, help_text="e.g. Cabinet 3, Drawer 2"
    )
    notes = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="verified_documents",
        help_text="HRMO staff who verified the physical or digital document.",
    )
    is_replaced = models.BooleanField(
        default=False,
        help_text="Whether this document has been superseded by a newer scan.",
    )
    replaced_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replaces",
        help_text="The newer document record that superseded this scan.",
    )
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-uploaded_at"]
        permissions = [
            ("view_documents", "Can view and download HR documents"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(person__isnull=False, engagement__isnull=True)
                | Q(person__isnull=True, engagement__isnull=False),
                name="document_person_xor_engagement",
                violation_error_message="Attach the document to a person or an engagement, not both.",
            ),
        ]

    def __str__(self):
        return f"{self.get_doc_type_display()} – {self.person or self.engagement}"

    @property
    def filename_for_display(self):
        if self.display_name:
            return self.display_name
        if self.file and self.file.name:
            return os.path.basename(self.file.name)
        return self.get_doc_type_display()


# --- Leave (CS Form No. 6, Revised 2020; DECISIONS L1-L9) --------------------


class LeaveType(models.TextChoices):
    """Section 6.A of CS Form No. 6 (Revised 2020), in the form's order."""

    VACATION = "vl", "Vacation leave"
    MANDATORY = "fl", "Mandatory/forced leave"
    SICK = "sl", "Sick leave"
    MATERNITY = "maternity", "Maternity leave"
    PATERNITY = "paternity", "Paternity leave"
    SPECIAL_PRIVILEGE = "spl", "Special privilege leave"
    SOLO_PARENT = "solo_parent", "Solo parent leave"
    STUDY = "study", "Study leave"
    VAWC = "vawc", "10-day VAWC leave"
    REHABILITATION = "rehab", "Rehabilitation privilege"
    SPECIAL_WOMEN = "slbw", "Special leave benefits for women"
    CALAMITY = "calamity", "Special emergency (calamity) leave"
    ADOPTION = "adoption", "Adoption leave"
    WELLNESS = "wellness", "Wellness leave"
    # Not on the form's list: printed under "Others" (DECISIONS CT4).
    CTO = "cto", "Compensatory time-off (CTO)"
    OTHER = "other", "Other"


class LeaveDetail(models.TextChoices):
    """Section 6.B of CS Form No. 6: where, what illness, or what the leave is for."""

    WITHIN_PH = "within_ph", "Within the Philippines"
    ABROAD = "abroad", "Abroad"
    IN_HOSPITAL = "in_hospital", "In hospital"
    OUT_PATIENT = "out_patient", "Out patient"
    WOMEN_ILLNESS = "women_illness", "Illness (special leave benefits for women)"
    MASTERS = "masters", "Completion of Master's degree"
    BAR_REVIEW = "bar_review", "BAR/Board examination review"
    MONETIZATION = "monetization", "Monetization of leave credits"
    TERMINAL = "terminal", "Terminal leave"


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Disapproved"
        CANCELLED = "cancelled", "Cancelled"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="leave_requests")
    # The office the leave is filed in (its head recommends it), kept even if the person transfers.
    office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.PROTECT, related_name="leave_requests"
    )
    position_title = models.CharField(max_length=150, blank=True)
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    leave_type_other = models.CharField("Other leave (specify)", max_length=100, blank=True)
    detail = models.CharField("Details of leave", max_length=20, choices=LeaveDetail.choices, blank=True)
    detail_text = models.CharField("Specify", max_length=200, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    # Suggested from the dates (weekdays less holidays), editable for half days (L4).
    days = models.DecimalField("Working days", max_digits=5, decimal_places=1)
    reason = models.TextField(blank=True)
    commutation_requested = models.BooleanField("Commutation requested", default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    filed_at = models.DateTimeField(auto_now_add=True)
    filed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Who entered it: the employee, or HRMO for a paper form.",
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    # 7.A and 7.C, filled in when HRMO certifies the credits (L5).
    certified_balances = models.JSONField(default=dict, blank=True)
    days_with_pay = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    days_without_pay = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    decision_note = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-filed_at"]
        permissions = [
            ("approve_leave", "Can act on and approve leave requests"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="leave_end_after_start",
                violation_error_message="End date cannot be before start date.",
            ),
            models.CheckConstraint(
                condition=Q(days__gt=0), name="leave_days_positive",
                violation_error_message="Days must be more than zero.",
            ),
        ]

    def __str__(self):
        return f"{self.person} – {self.get_leave_type_display()} {self.start_date}"

    @property
    def type_label(self):
        if self.leave_type == LeaveType.OTHER and self.leave_type_other:
            return self.leave_type_other
        return self.get_leave_type_display()

    @property
    def current_step(self):
        if self.status != self.Status.PENDING:
            return None
        return next((s for s in self.steps.all() if s.status == LeaveApprovalStep.Status.PENDING), None)


class LeaveApprovalStep(models.Model):
    """One signature box of section 7 of CS Form No. 6, in the configured order (DECISIONS L1)."""

    class Kind(models.TextChoices):
        RECOMMEND = "recommend", "Recommendation (office head)"
        CERTIFY = "certify", "Certification of leave credits (HRMO)"
        APPROVE = "approve", "Approval (Municipal Mayor)"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting"
        DONE = "done", "Done"
        DISAPPROVED = "disapproved", "Disapproved"
        SKIPPED = "skipped", "Not needed"

    request = models.ForeignKey(LeaveRequest, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    acted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    acted_at = models.DateTimeField(null=True, blank=True)
    # HRMO recorded it from the wet-signed paper form, for signatories without an account (L6).
    from_paper = models.BooleanField(default=False)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["request", "order"]
        constraints = [
            models.UniqueConstraint(fields=["request", "order"], name="leavestep_one_per_order"),
        ]

    def __str__(self):
        return f"{self.request} · {self.get_kind_display()}: {self.get_status_display()}"

    @property
    def short_label(self):
        return {"recommend": "Office head", "certify": "HRMO", "approve": "Mayor"}[self.kind]


class LeaveCredit(models.Model):
    """Ledger row: +days earned/adjusted, -days used. Balance = sum per leave type."""

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="leave_credits")
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    days = models.DecimalField(max_digits=7, decimal_places=3)
    as_of = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    leave_request = models.ForeignKey(
        LeaveRequest, null=True, blank=True, on_delete=models.PROTECT, related_name="credit_entries"
    )

    class Meta:
        ordering = ["-as_of", "-id"]

    def __str__(self):
        return f"{self.person} {self.leave_type} {self.days:+}"

    def clean(self):
        if self.person_id and not self.person.earns_leave_credits():
            raise ValidationError("Only people with an active leave-eligible engagement have leave credits (DECISIONS L3).")


# --- Calendar (DECISIONS C1-C2) ------------------------------------------------


class CalendarEvent(models.Model):
    """A dated item with no record of its own: interview, deadline, holiday, meeting.

    Leave, hires, contract ends and project dates are read from their own tables by the
    calendar view, never copied here.
    """

    class Kind(models.TextChoices):
        INTERVIEW = "interview", "Hiring interview"
        DEADLINE = "deadline", "Deadline"
        HOLIDAY = "holiday", "Holiday / no work"
        MEETING = "meeting", "Meeting / activity"
        OTHER = "other", "Other"

    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Leave blank for a one-day event.")
    time = models.TimeField(null=True, blank=True)
    office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.PROTECT, related_name="calendar_events",
        help_text="Only this office (and all-office roles) see it. Leave blank to show to everyone.",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["start_date", "time"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="calendarevent_end_after_start",
                violation_error_message="End date cannot be before start date.",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title} ({self.start_date})"


# --- Salary actions: step increments and loyalty awards (DECISIONS SA1-SA6) --------


class ServiceInterruption(models.Model):
    """Leave without pay or suspension. Pushes back step increment and loyalty due dates (SA2)."""

    class Kind(models.TextChoices):
        LWOP = "lwop", "Leave without pay"
        SUSPENSION = "suspension", "Suspension"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="service_interruptions")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_date"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=F("start_date")),
                name="serviceinterruption_end_after_start",
                violation_error_message="End date cannot be before start date.",
            ),
        ]

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    def __str__(self):
        return f"{self.get_kind_display()} {self.start_date} – {self.end_date}"


class SalaryAction(models.Model):
    """One step increment or loyalty award case, found by the daily check and worked by HRMO.

    The check only finds and dates cases; HRMO reviews them, the Mayor signs the NOSI on
    paper, and only an Issued step increment changes Engagement.step (SA3, SA4).
    """

    class Kind(models.TextChoices):
        STEP_INCREMENT = "step", "Salary increase (step)"
        LOYALTY = "loyalty", "Loyalty award"

    class Status(models.TextChoices):
        UPCOMING = "upcoming", "Upcoming"
        DUE = "due", "Due"
        REVIEW = "review", "Under review"
        SIGNATURE = "signature", "Awaiting signature"
        ISSUED = "issued", "Issued"
        DEFERRED = "deferred", "Deferred"
        NOT_ELIGIBLE = "not_eligible", "Not eligible"

    # The daily check may still move these cases' dates; once HRMO starts work it never does.
    AUTO_STATUSES = [Status.UPCOMING, Status.DUE]
    OPEN_STATUSES = [Status.UPCOMING, Status.DUE, Status.REVIEW, Status.SIGNATURE, Status.DEFERRED]
    CLOSED_STATUSES = [Status.ISSUED, Status.NOT_ELIGIBLE]

    kind = models.CharField(max_length=20, choices=Kind.choices)
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="salary_actions")
    # The appointment the case belongs to (its step for increments; current office for awards).
    engagement = models.ForeignKey(Engagement, on_delete=models.PROTECT, related_name="salary_actions")
    # Identifies the milestone so the check never opens the same case twice,
    # e.g. "step:2023-05-01:3" or "loyalty:10".
    milestone = models.CharField(max_length=60)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPCOMING, db_index=True)
    service_from = models.DateField(help_text="Start of the service counted for this milestone.")
    due_date = models.DateField(db_index=True)
    extension_days = models.PositiveIntegerField(default=0, help_text="Leave without pay and suspension days.")
    from_step = models.PositiveSmallIntegerField(null=True, blank=True)
    to_step = models.PositiveSmallIntegerField(null=True, blank=True)
    years = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Loyalty milestone in years.")
    warnings = models.JSONField(default=list, blank=True)
    checklist = models.JSONField(default=dict, blank=True)
    deferred_until = models.DateField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    nosi_number = models.CharField("NOSI / award number", max_length=50, blank=True)
    signed_on = models.DateField(null=True, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["due_date", "pk"]
        permissions = [
            ("manage_salary_actions", "Can review and issue step increments and loyalty awards"),
            ("view_salary_actions", "Can view step increment and loyalty award cases"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["person", "kind", "milestone"], name="salaryaction_one_case_per_milestone"),
            models.CheckConstraint(
                condition=Q(kind="loyalty", years__isnull=False, from_step__isnull=True, to_step__isnull=True)
                | Q(kind="step", years__isnull=True, from_step__isnull=False, to_step__isnull=False),
                name="salaryaction_kind_fields",
                violation_error_message="Step increments need from/to steps; loyalty awards need years.",
            ),
            models.CheckConstraint(
                condition=Q(to_step__isnull=True) | Q(to_step__range=(2, 8)),
                name="salaryaction_to_step_range",
                violation_error_message="A step increment goes to step 2 to 8.",
            ),
        ]

    def __str__(self):
        what = f"Step {self.from_step} → {self.to_step}" if self.kind == self.Kind.STEP_INCREMENT else f"{self.years}-year loyalty award"
        return f"{self.person} – {what} (due {self.due_date})"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class SalaryCheckRun(models.Model):
    """One row per run of the daily check, so the tracker can say when it last ran."""

    ran_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=20, help_text="'command', 'button' or 'page'")
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-ran_at"]


# --- Required meetings (DECISIONS MT1-MT5) ------------------------------------------


class MeetingSeries(models.Model):
    """A meeting the LGU must hold on a schedule, e.g. a committee that meets every quarter."""

    class Frequency(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        SEMESTRAL = "semestral", "Every six months"
        ANNUAL = "annual", "Yearly"

    class Audience(models.TextChoices):
        EVERYONE = "everyone", "Everyone"
        OFFICE = "office", "One office"
        CHOSEN = "chosen", "Chosen people"

    MONTHS = {Frequency.MONTHLY: 1, Frequency.QUARTERLY: 3, Frequency.SEMESTRAL: 6, Frequency.ANNUAL: 12}

    name = models.CharField(max_length=200)
    legal_basis = models.CharField(
        max_length=255, blank=True, help_text="The circular, ordinance or memo that requires it."
    )
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.QUARTERLY)
    audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.EVERYONE)
    office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.PROTECT, related_name="meeting_series",
        help_text="Required when the audience is one office.",
    )
    members = models.ManyToManyField(Person, blank=True, related_name="meeting_series")
    starts_on = models.DateField(help_text="Tracking starts with the period that contains this date.")
    is_active = models.BooleanField(default=True, help_text="Untick to stop tracking; history is kept.")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "meeting series"
        constraints = [
            models.CheckConstraint(
                condition=~Q(audience="office") | Q(office__isnull=False),
                name="meetingseries_office_audience_has_office",
                violation_error_message="Choose the office whose staff attend.",
            ),
        ]

    def __str__(self):
        return self.name


class Meeting(models.Model):
    """One period of a series (e.g. Q3 2026): when it was scheduled, held, and who attended.

    Status is worked out from the dates, so nothing has to be closed by hand (MT2).
    """

    class Status(models.TextChoices):
        DUE = "due", "Not scheduled yet"
        SCHEDULED = "scheduled", "Scheduled"
        HELD = "held", "Held"
        NOT_HELD = "not_held", "Not held (reason given)"
        MISSED = "missed", "Missed"
        UPCOMING = "upcoming", "Not started"

    series = models.ForeignKey(MeetingSeries, on_delete=models.PROTECT, related_name="meetings")
    period_start = models.DateField()
    period_end = models.DateField()
    scheduled_on = models.DateField(null=True, blank=True)
    scheduled_time = models.TimeField(null=True, blank=True)
    venue = models.CharField(max_length=200, blank=True)
    held_on = models.DateField(null=True, blank=True)
    attendees = models.ManyToManyField(Person, blank=True, related_name="meetings_attended")
    attendance_count = models.PositiveIntegerField(
        null=True, blank=True, help_text="For meetings of everyone: how many attended."
    )
    minutes_location = models.CharField(
        max_length=255, blank=True, help_text="Where the signed minutes and attendance sheet are filed."
    )
    minutes_summary = models.TextField(blank=True)
    not_held_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["series__name", "-period_start"]
        constraints = [
            models.UniqueConstraint(fields=["series", "period_start"], name="meeting_one_per_period"),
            models.CheckConstraint(
                condition=Q(period_end__gte=F("period_start")),
                name="meeting_period_end_after_start",
                violation_error_message="The period cannot end before it starts.",
            ),
        ]

    def __str__(self):
        return f"{self.series.name} ({self.period_label})"

    @property
    def period_label(self):
        months = self.series.MONTHS[self.series.frequency]
        if months == 3:
            return f"Q{(self.period_start.month - 1) // 3 + 1} {self.period_start.year}"
        if months == 6:
            return f"{'Jan–Jun' if self.period_start.month == 1 else 'Jul–Dec'} {self.period_start.year}"
        if months == 12:
            return str(self.period_start.year)
        return self.period_start.strftime("%B %Y")

    def status_on(self, today):
        if self.held_on:
            return self.Status.HELD
        if self.not_held_reason:
            return self.Status.NOT_HELD
        if today < self.period_start:
            return self.Status.UPCOMING
        if today > self.period_end:
            return self.Status.MISSED
        return self.Status.SCHEDULED if self.scheduled_on else self.Status.DUE


# --- Vacancies and the selection board (DECISIONS V1-V5) ------------------------------


class Vacancy(models.Model):
    """A vacant plantilla item HRMO publishes (CS request for publication), through HRMPSB to filling."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (not published)"
        OPEN = "open", "Open for applications"
        SCREENING = "screening", "Closed: HRMPSB screening"
        FILLED = "filled", "Filled"
        CANCELLED = "cancelled", "Cancelled"

    VISIBLE_STATUSES = [Status.OPEN, Status.SCREENING, Status.FILLED]

    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name="vacancies",
                               verbose_name="Place of assignment")
    position_title = models.CharField(max_length=150, help_text="With the parenthetical title, if any.")
    item_number = models.CharField("Plantilla item no.", max_length=50)
    salary_grade = models.PositiveSmallIntegerField()
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2)
    education = models.TextField()
    training = models.TextField(default="None required")
    experience = models.TextField(default="None required")
    eligibility = models.TextField()
    competencies = models.TextField("Competency / area of specialization", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    published_on = models.DateField("Published from", null=True, blank=True)
    deadline = models.DateField("Applications until", null=True, blank=True)
    csc_received_on = models.DateField("Received by CSC", null=True, blank=True)
    posted_at = models.TextField(
        "Posted at", blank=True,
        help_text="Where the notice is posted, e.g. the HRMO bulletin board and the other boards.",
    )
    # HRMPSB deliberation (the resolution's heading and number).
    board_met_on = models.DateField("HRMPSB met on", null=True, blank=True)
    board_venue = models.CharField("HRMPSB venue", max_length=200, blank=True)
    resolution_no = models.CharField("Resolution no.", max_length=50, blank=True)
    filled_by = models.ForeignKey(
        Engagement, null=True, blank=True, on_delete=models.SET_NULL, related_name="filled_vacancies",
        help_text="The appointment that filled the item.",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "vacancies"
        constraints = [
            models.CheckConstraint(
                condition=Q(salary_grade__range=(1, 33)),
                name="vacancy_salary_grade_range",
                violation_error_message="Salary grade must be 1 to 33.",
            ),
            models.CheckConstraint(
                condition=Q(deadline__isnull=True) | Q(published_on__isnull=True) | Q(deadline__gte=F("published_on")),
                name="vacancy_deadline_after_publication",
                violation_error_message="The deadline cannot be before the publication date.",
            ),
        ]

    def __str__(self):
        return f"{self.position_title} ({self.item_number})"

    def is_accepting(self, today):
        return self.status == self.Status.OPEN and (self.deadline is None or today <= self.deadline)


class Applicant(models.Model):
    """Someone who signified interest in a vacancy, with the documents the notice asks for (V3)."""

    REQUIREMENTS = [
        ("has_pds", "Personal Data Sheet with work experience sheet and photo (CS Form No. 212)"),
        ("has_rating", "Performance rating in the last rating period (if applicable)"),
        ("has_eligibility", "Proof of eligibility / rating / license"),
        ("has_tor", "Transcript of records"),
    ]

    vacancy = models.ForeignKey(Vacancy, on_delete=models.PROTECT, related_name="applicants")
    person = models.ForeignKey(
        Person, null=True, blank=True, on_delete=models.PROTECT, related_name="applications",
        help_text="Set when a current employee applies.",
    )
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    signified_on = models.DateField("Signified interest on")
    applied_online = models.BooleanField(default=False)
    has_pds = models.BooleanField("PDS", default=False)
    has_rating = models.BooleanField("Performance rating", default=False)
    has_eligibility = models.BooleanField("Eligibility", default=False)
    has_tor = models.BooleanField("Transcript", default=False)
    meets_qs = models.BooleanField("Meets the qualification standards", null=True, blank=True)
    rank = models.PositiveSmallIntegerField(null=True, blank=True)
    remarks = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["vacancy", "rank", "last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["vacancy", "person"], condition=Q(person__isnull=False),
                name="applicant_once_per_employee",
                violation_error_message="This employee already applied for this vacancy.",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} for {self.vacancy}"

    @property
    def full_name(self):
        middle = f" {self.middle_name[0]}." if self.middle_name else ""
        return f"{self.last_name}, {self.first_name}{middle}"

    @property
    def display_name(self):
        middle = f" {self.middle_name[0]}." if self.middle_name else ""
        return f"{self.first_name}{middle} {self.last_name}"

    @property
    def complete(self):
        return all(getattr(self, field) for field, _ in self.REQUIREMENTS)


# --- Office memoranda with online acknowledgment (DECISIONS M1-M4) --------------------


def memo_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"memos/{date.today():%Y/%m}/{uuid.uuid4().hex}{ext}"


class Memo(models.Model):
    """An office memorandum or advisory and who has acknowledged reading it."""

    Audience = MeetingSeries.Audience

    number = models.CharField(max_length=100, help_text="e.g. HRMO Memorandum No. 010, series of 2026")
    dated = models.DateField()
    subject = models.CharField(max_length=255)
    addressed_to = models.CharField(
        max_length=255, help_text="As written on the memo, e.g. All appointive employees and JO/COS personnel."
    )
    audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.EVERYONE)
    office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.PROTECT, related_name="memos",
        help_text="Required when the audience is one office.",
    )
    members = models.ManyToManyField(Person, blank=True, related_name="memos_addressed")
    body = models.TextField(help_text="The text of the memo, or a summary of it if a scan is attached.")
    scan = models.FileField(
        upload_to=memo_upload_path, blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"]), validate_file_size],
        help_text="Optional scan of the signed memo (PDF, JPG or PNG).",
    )
    scan_name = models.CharField(max_length=255, blank=True)
    signed_by = models.CharField(max_length=200, blank=True, help_text="Name and title of the signatory.")
    released_on = models.DateField(null=True, blank=True, help_text="Date released by the Office of the Mayor.")
    comply_by = models.DateField("Comply by", null=True, blank=True, help_text="Deadline the memo sets, if any.")
    requires_ack = models.BooleanField("Ask everyone to acknowledge", default=True)
    is_published = models.BooleanField("Published", default=True, help_text="Untick to keep it as a draft.")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-dated", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(audience="office") | Q(office__isnull=False),
                name="memo_office_audience_has_office",
                violation_error_message="Choose the office the memo is for.",
            ),
        ]

    def __str__(self):
        return f"{self.number}: {self.subject}"


class MemoAcknowledgment(models.Model):
    """An employee's "I have read and understood this memo", replacing the routing slip (M2)."""

    memo = models.ForeignKey(Memo, on_delete=models.PROTECT, related_name="acknowledgments")
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="memo_acknowledgments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    acknowledged_at = models.DateTimeField(auto_now_add=True)
    # HRMO recorded a signature from the paper routing list.
    from_paper = models.BooleanField(default=False)

    class Meta:
        ordering = ["-acknowledged_at"]
        constraints = [
            models.UniqueConstraint(fields=["memo", "person"], name="memoack_once_per_person"),
        ]

    def __str__(self):
        return f"{self.person} read {self.memo.number}"


# --- Service records and certificates of employment (DECISIONS CR1-CR6) ----------------


class CertificateRequest(models.Model):
    """A request for a service record or certificate of employment, filed online by the person
    or recorded by HRMO for a walk-in, letter or email, including from former employees and heirs."""

    class Kind(models.TextChoices):
        SERVICE_RECORD = "service_record", "Service record"
        COE = "coe", "Certificate of employment"
        COE_PAY = "coe_pay", "Certificate of employment with compensation"

    class Via(models.TextChoices):
        ONLINE = "online", "Online"
        WALK_IN = "walk_in", "Walk-in"
        LETTER = "letter", "Letter or email"

    class Requester(models.TextChoices):
        SELF = "self", "The person named"
        REPRESENTATIVE = "representative", "A representative (with authorization letter)"
        HEIR = "heir", "Heir or next of kin"

    class Status(models.TextChoices):
        PENDING = "pending", "Being prepared"
        READY = "ready", "Ready for release"
        RELEASED = "released", "Released"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    OPEN_STATUSES = [Status.PENDING, Status.READY]

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="certificate_requests")
    kind = models.CharField("Document", max_length=20, choices=Kind.choices)
    purpose = models.CharField(
        max_length=200, help_text="e.g. GSIS retirement claim, loan application, employment abroad."
    )
    copies = models.PositiveSmallIntegerField(default=1)
    received_via = models.CharField(max_length=20, choices=Via.choices, default=Via.ONLINE)
    requester = models.CharField("Requested by", max_length=20, choices=Requester.choices, default=Requester.SELF)
    requester_name = models.CharField(
        "Requester's name", max_length=200, blank=True, help_text="When someone else asks for the person."
    )
    contact = models.CharField(
        "Contact for release", max_length=200, blank=True,
        help_text="Mobile or email where HRMO can say it is ready.",
    )
    control_no = models.CharField("Control no.", max_length=20, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    filed_at = models.DateTimeField(auto_now_add=True)
    filed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    prepared_at = models.DateTimeField(null=True, blank=True)
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    released_at = models.DateTimeField(null=True, blank=True)
    released_to = models.CharField(max_length=200, blank=True)
    note = models.TextField(
        blank=True, help_text="Release details (ID presented, authorization letter) or the reason for declining."
    )
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-filed_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(copies__range=(1, 10)),
                name="certificaterequest_copies_range",
                violation_error_message="Copies must be 1 to 10.",
            ),
            models.CheckConstraint(
                condition=Q(requester="self") | ~Q(requester_name=""),
                name="certificaterequest_requester_named",
                violation_error_message="Give the name of the representative or heir.",
            ),
            models.CheckConstraint(
                condition=~Q(status="released") | (~Q(released_to="") & Q(released_at__isnull=False)),
                name="certificaterequest_released_to",
                violation_error_message="Record who received the document.",
            ),
            models.CheckConstraint(
                condition=~Q(status="declined") | ~Q(note=""),
                name="certificaterequest_declined_reason",
                violation_error_message="Give the reason for declining.",
            ),
        ]

    def __str__(self):
        return f"{self.control_no} {self.get_kind_display()} for {self.person}"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


# --- Personal Data Sheet, CS Form No. 212 Revised 2017 (DECISIONS PD1-PD7) --------------
# Name, birthdate, sex, contact numbers and government IDs stay on Person (HRMO keeps them);
# work in this LGU comes from Engagement. These tables hold the rest of the form.


def _fake_only(model, field):
    return models.CheckConstraint(
        condition=Q(**{field: ""}) | Q(**{f"{field}__regex": FAKE_ID_REGEX}),
        name=f"{model}_{field}_fake_only",
        violation_error_message="Prototype accepts fake ID numbers only (must start with 00).",
    )


class PdsProfile(models.Model):
    """Sections I, II, VIII and questions 34-42 of the PDS: one row per person."""

    class CivilStatus(models.TextChoices):
        SINGLE = "single", "Single"
        MARRIED = "married", "Married"
        WIDOWED = "widowed", "Widowed"
        SEPARATED = "separated", "Separated"
        OTHER = "other", "Other/s"

    class Citizenship(models.TextChoices):
        FILIPINO = "filipino", "Filipino"
        DUAL = "dual", "Dual citizenship"

    class DualBy(models.TextChoices):
        BIRTH = "birth", "By birth"
        NATURALIZATION = "naturalization", "By naturalization"

    BLOOD_TYPES = [(t, t) for t in ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]]

    person = models.OneToOneField(Person, on_delete=models.PROTECT, related_name="pds")
    # I. Personal information
    place_of_birth = models.CharField(max_length=150, blank=True)
    civil_status = models.CharField(max_length=20, choices=CivilStatus.choices, blank=True)
    civil_status_other = models.CharField("Other civil status", max_length=50, blank=True)
    height_m = models.DecimalField("Height (m)", max_digits=3, decimal_places=2, null=True, blank=True)
    weight_kg = models.DecimalField("Weight (kg)", max_digits=5, decimal_places=1, null=True, blank=True)
    blood_type = models.CharField(max_length=3, choices=BLOOD_TYPES, blank=True)
    citizenship = models.CharField(max_length=20, choices=Citizenship.choices, default=Citizenship.FILIPINO)
    dual_by = models.CharField("Dual citizenship", max_length=20, choices=DualBy.choices, blank=True)
    dual_country = models.CharField("Country (dual citizenship)", max_length=100, blank=True)
    res_house = models.CharField("House/block/lot no.", max_length=100, blank=True)
    res_street = models.CharField("Street", max_length=100, blank=True)
    res_subdivision = models.CharField("Subdivision/village", max_length=100, blank=True)
    res_barangay = models.CharField("Barangay", max_length=100, blank=True)
    res_city = models.CharField("City/municipality", max_length=100, blank=True)
    res_province = models.CharField("Province", max_length=100, blank=True)
    res_zip = models.CharField("ZIP code", max_length=10, blank=True)
    perm_same = models.BooleanField("Permanent address is the same as residential", default=True)
    perm_house = models.CharField("House/block/lot no.", max_length=100, blank=True)
    perm_street = models.CharField("Street", max_length=100, blank=True)
    perm_subdivision = models.CharField("Subdivision/village", max_length=100, blank=True)
    perm_barangay = models.CharField("Barangay", max_length=100, blank=True)
    perm_city = models.CharField("City/municipality", max_length=100, blank=True)
    perm_province = models.CharField("Province", max_length=100, blank=True)
    perm_zip = models.CharField("ZIP code", max_length=10, blank=True)
    telephone = models.CharField("Telephone no.", max_length=30, blank=True)
    # II. Family background
    spouse_surname = models.CharField("Spouse's surname", max_length=100, blank=True)
    spouse_first_name = models.CharField("First name", max_length=100, blank=True)
    spouse_middle_name = models.CharField("Middle name", max_length=100, blank=True)
    spouse_extension = models.CharField("Name extension", max_length=10, blank=True)
    spouse_occupation = models.CharField("Occupation", max_length=100, blank=True)
    spouse_employer = models.CharField("Employer/business name", max_length=150, blank=True)
    spouse_business_address = models.CharField("Business address", max_length=200, blank=True)
    spouse_telephone = models.CharField("Telephone no.", max_length=30, blank=True)
    father_surname = models.CharField("Father's surname", max_length=100, blank=True)
    father_first_name = models.CharField("First name", max_length=100, blank=True)
    father_middle_name = models.CharField("Middle name", max_length=100, blank=True)
    father_extension = models.CharField("Name extension", max_length=10, blank=True)
    mother_surname = models.CharField("Mother's maiden surname", max_length=100, blank=True)
    mother_first_name = models.CharField("First name", max_length=100, blank=True)
    mother_middle_name = models.CharField("Middle name", max_length=100, blank=True)
    # VIII. Other information, one item per line
    skills = models.TextField("Special skills and hobbies", blank=True)
    distinctions = models.TextField("Non-academic distinctions / recognition", blank=True)
    memberships = models.TextField("Membership in association/organization", blank=True)
    # Questions 34-40: None means not yet answered.
    q34a = models.BooleanField(null=True, blank=True)
    q34b = models.BooleanField(null=True, blank=True)
    q34_details = models.CharField(max_length=255, blank=True)
    q35a = models.BooleanField(null=True, blank=True)
    q35a_details = models.CharField(max_length=255, blank=True)
    q35b = models.BooleanField(null=True, blank=True)
    q35b_details = models.CharField(max_length=255, blank=True)
    q35b_date_filed = models.DateField(null=True, blank=True)
    q35b_status = models.CharField(max_length=100, blank=True)
    q36 = models.BooleanField(null=True, blank=True)
    q36_details = models.CharField(max_length=255, blank=True)
    q37 = models.BooleanField(null=True, blank=True)
    q37_details = models.CharField(max_length=255, blank=True)
    q38a = models.BooleanField(null=True, blank=True)
    q38a_details = models.CharField(max_length=255, blank=True)
    q38b = models.BooleanField(null=True, blank=True)
    q38b_details = models.CharField(max_length=255, blank=True)
    q39 = models.BooleanField(null=True, blank=True)
    q39_details = models.CharField(max_length=255, blank=True)
    q40a = models.BooleanField(null=True, blank=True)
    q40a_details = models.CharField(max_length=255, blank=True)
    q40b = models.BooleanField(null=True, blank=True)
    q40b_details = models.CharField(max_length=255, blank=True)
    q40c = models.BooleanField(null=True, blank=True)
    q40c_details = models.CharField(max_length=255, blank=True)
    # 42. Government-issued ID shown when the PDS is sworn
    gov_id_type = models.CharField("Government-issued ID", max_length=100, blank=True, help_text="e.g. Passport, UMID, driver's license")
    gov_id_number = models.CharField("ID/license/passport no.", max_length=50, blank=True)
    gov_id_issued = models.CharField("Date/place of issuance", max_length=150, blank=True)

    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [_fake_only("pdsprofile", "gov_id_number")]

    def __str__(self):
        return f"PDS of {self.person}"


class PdsChild(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="pds_children")
    full_name = models.CharField("Name of child", max_length=200)
    birthdate = models.DateField("Date of birth", null=True, blank=True)

    class Meta:
        ordering = ["birthdate", "pk"]


class PdsEducation(models.Model):
    class Level(models.TextChoices):
        ELEMENTARY = "elementary", "Elementary"
        SECONDARY = "secondary", "Secondary"
        VOCATIONAL = "vocational", "Vocational / trade course"
        COLLEGE = "college", "College"
        GRADUATE = "graduate", "Graduate studies"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="pds_education")
    level = models.CharField(max_length=20, choices=Level.choices)
    school = models.CharField("Name of school", max_length=200)
    degree = models.CharField("Basic education/degree/course", max_length=200, blank=True)
    year_from = models.PositiveSmallIntegerField("From (year)", null=True, blank=True)
    year_to = models.PositiveSmallIntegerField("To (year)", null=True, blank=True)
    units_earned = models.CharField("Highest level/units earned", max_length=100, blank=True,
                                    help_text="If not graduated.")
    year_graduated = models.PositiveSmallIntegerField(null=True, blank=True)
    honors = models.CharField("Scholarship/academic honors", max_length=200, blank=True)

    class Meta:
        ordering = ["pk"]


class PdsEligibility(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="pds_eligibility")
    name = models.CharField("Eligibility", max_length=200,
                            help_text="Career service, RA 1080 (board/bar), special laws, CES/CSEE, barangay, driver's license")
    rating = models.CharField(max_length=20, blank=True)
    exam_date = models.DateField("Date of exam/conferment", null=True, blank=True)
    exam_place = models.CharField("Place of exam/conferment", max_length=200, blank=True)
    license_number = models.CharField("License no.", max_length=50, blank=True)
    license_valid_until = models.DateField("Valid until", null=True, blank=True)

    class Meta:
        ordering = ["exam_date", "pk"]
        constraints = [_fake_only("pdseligibility", "license_number")]


class PdsWorkExperience(models.Model):
    """Work before or outside this LGU. Appointments here come from Engagement (PD3)."""

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="pds_work")
    date_from = models.DateField("From")
    date_to = models.DateField("To", null=True, blank=True, help_text="Blank for present.")
    position = models.CharField("Position title", max_length=150)
    employer = models.CharField("Department/agency/office/company", max_length=200)
    monthly_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    grade_step = models.CharField("Salary grade and step", max_length=20, blank=True, help_text="e.g. 11-2")
    appointment_status = models.CharField("Status of appointment", max_length=50, blank=True)
    government = models.BooleanField("Government service", default=False)

    class Meta:
        ordering = ["-date_from", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(date_to__isnull=True) | Q(date_to__gte=F("date_from")),
                                   name="pdswork_to_after_from",
                                   violation_error_message="End date cannot be before start date."),
        ]


class PdsVoluntaryWork(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="pds_voluntary")
    organization = models.CharField("Name and address of organization", max_length=255)
    date_from = models.DateField("From", null=True, blank=True)
    date_to = models.DateField("To", null=True, blank=True)
    hours = models.PositiveIntegerField("Number of hours", null=True, blank=True)
    position = models.CharField("Position/nature of work", max_length=150, blank=True)

    class Meta:
        ordering = ["-date_from", "pk"]


class PdsTraining(models.Model):
    class Kind(models.TextChoices):
        MANAGERIAL = "managerial", "Managerial"
        SUPERVISORY = "supervisory", "Supervisory"
        TECHNICAL = "technical", "Technical"
        FOUNDATION = "foundation", "Foundation"
        OTHER = "other", "Other"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="pds_training")
    title = models.CharField("Title of learning and development intervention", max_length=255)
    date_from = models.DateField("From")
    date_to = models.DateField("To", null=True, blank=True)
    hours = models.PositiveIntegerField("Number of hours", null=True, blank=True)
    kind = models.CharField("Type of L&D", max_length=20, choices=Kind.choices, blank=True)
    sponsor = models.CharField("Conducted/sponsored by", max_length=255, blank=True)
    # Recorded by HRMO for everyone who attended (LD2): read-only to the employee, and not a change
    # they must send for verification.
    by_hrmo = models.BooleanField(default=False)

    class Meta:
        ordering = ["-date_from", "pk"]


class PdsReference(models.Model):
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="pds_references")
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255, blank=True)
    telephone = models.CharField("Tel. no.", max_length=30, blank=True)

    class Meta:
        ordering = ["pk"]


class PdsSubmission(models.Model):
    """The PDS as submitted to HRMO, frozen, and what HRMO decided (PD4)."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Waiting for HRMO"
        VERIFIED = "verified", "Verified by HRMO"
        RETURNED = "returned", "Returned for correction"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="pds_submissions")
    snapshot = models.JSONField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-submitted_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(status="returned") | ~Q(note=""),
                name="pdssubmission_returned_reason",
                violation_error_message="Say what to correct.",
            ),
            models.UniqueConstraint(fields=["person"], condition=Q(status="submitted"),
                                    name="pdssubmission_one_waiting"),
        ]

    def __str__(self):
        return f"PDS of {self.person} submitted {self.submitted_at:%Y-%m-%d}"


# --- Performance ratings, SPMS (DECISIONS SP1-SP5) ------------------------------------------


class PerformanceRating(models.Model):
    """One person's final IPCR rating for one semester."""

    class Semester(models.IntegerChoices):
        FIRST = 1, "January to June"
        SECOND = 2, "July to December"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="ratings")
    year = models.PositiveSmallIntegerField()
    semester = models.PositiveSmallIntegerField(choices=Semester.choices)
    rating = models.DecimalField("Final rating", max_digits=4, decimal_places=3)
    office = models.ForeignKey(Office, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                               help_text="Where the person was rated.")
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-semester"]
        constraints = [
            models.UniqueConstraint(fields=["person", "year", "semester"], name="rating_one_per_period"),
            models.CheckConstraint(condition=Q(rating__gte=1) & Q(rating__lte=5), name="rating_1_to_5",
                                   violation_error_message="A rating is from 1.000 to 5.000."),
        ]

    def __str__(self):
        return f"{self.person} {self.year}-{self.semester}: {self.rating}"

    @property
    def adjectival(self):
        from .spms import adjectival

        return adjectival(self.rating)


# --- SALN filing (DECISIONS SN1-SN4) ----------------------------------------------------------


class SalnFiling(models.Model):
    """Whether a person filed their Statement of Assets, Liabilities and Net Worth for a year.
    Only the filing is tracked; the SALN itself stays on paper with HRMO."""

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="saln_filings")
    year = models.PositiveSmallIntegerField(help_text="The year covered (assets as of December 31).")
    reported_at = models.DateTimeField(null=True, blank=True, help_text="When the person said they submitted it.")
    received_on = models.DateField(null=True, blank=True, help_text="When HRMO received it.")
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year"]
        constraints = [models.UniqueConstraint(fields=["person", "year"], name="saln_one_per_year")]

    def __str__(self):
        return f"SALN {self.year} of {self.person}"


# --- Compensatory overtime credits (DECISIONS CT1-CT5) ----------------------------------------


class CocEntry(models.Model):
    """Hours of compensatory overtime credit earned (positive) or used as CTO (negative)."""

    class Kind(models.TextChoices):
        EARNED = "earned", "Earned"
        USED = "used", "Used as CTO"

    class DayKind(models.TextChoices):
        WORKDAY = "workday", "Working day (after office hours)"
        RESTDAY = "restday", "Weekend, holiday or day off"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="coc_entries")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    date = models.DateField(help_text="Day worked, or first day of the CTO.")
    hours_worked = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    day_kind = models.CharField(max_length=10, choices=DayKind.choices, blank=True)
    hours = models.DecimalField("COC hours", max_digits=6, decimal_places=2)
    expires_on = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True, help_text="Office order or certificate of COC no.")
    leave_request = models.ForeignKey(LeaveRequest, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="coc_entries")
    void = models.BooleanField(default=False, help_text="CTO cancelled: the hours count again.")
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(kind="earned", hours__gt=0, expires_on__isnull=False)
                           | Q(kind="used", hours__lt=0, leave_request__isnull=False)),
                name="coc_entry_shape",
                violation_error_message="Earned hours are positive with an expiry; used hours are negative with a CTO.",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.hours} h, {self.person}"


# --- User Profile -----------------------------------------------------------


class UserProfile(models.Model):
    """Links Django auth User to a Person (self-service) and/or Office (head/staff)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    person = models.OneToOneField(
        Person, null=True, blank=True, on_delete=models.SET_NULL, related_name="user_profile"
    )
    office = models.ForeignKey(
        Office, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff_profiles"
    )
    must_change_password = models.BooleanField(
        default=False,
        help_text="If True, user must change password upon login (DECISIONS A1).",
    )
    password_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When an unused one-time password stops working (DECISIONS A4).",
    )

    def __str__(self):
        return f"Profile of {self.user.username}"


class PasswordResetRequest(models.Model):
    """"Forgot password" from the login page, or a walk-in at the HRMO counter (DECISIONS A2).

    There is no email reset: HRMO checks the person's ID and issues a one-time password. The
    request stores what was typed; `user` is set only when it matched an account, and the login
    page answers the same either way, so it never tells a stranger which usernames exist.
    """

    class Via(models.TextChoices):
        ONLINE = "online", "Forgot password page"
        COUNTER = "counter", "HRMO counter"

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for HRMO"
        ISSUED = "issued", "One-time password issued"
        DISMISSED = "dismissed", "Dismissed"

    username_entered = models.CharField("Username typed", max_length=150)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="password_reset_requests"
    )
    contact = models.CharField(
        max_length=100, blank=True, help_text="Mobile number or office where HRMO can reach the person."
    )
    received_via = models.CharField(max_length=20, choices=Via.choices, default=Via.ONLINE)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    id_checked = models.CharField(
        "ID seen", max_length=100, blank=True, help_text="The ID HRMO checked before issuing, e.g. LGU ID, UMID."
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Password reset for {self.username_entered} ({self.get_status_display().lower()})"


class MfaDevice(models.Model):
    """The authenticator app an account uses for the second login step (DECISIONS A5).

    Only a confirmed app is stored: setup keeps the new secret in the session until the person
    types a code from it. The secret is never shown again and never written to the audit log.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_device")
    secret = models.CharField(max_length=64, help_text="Base32 TOTP secret shared with the app.")
    confirmed_at = models.DateTimeField(default=timezone.now)
    last_used_step = models.BigIntegerField(
        default=0, help_text="30-second window of the last accepted code: a code works only once."
    )
    failures = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    remember_generation = models.PositiveIntegerField(
        default=0, help_text="Raised by \"Forget remembered browsers\": every older remember cookie stops working."
    )

    def __str__(self):
        return f"Authenticator app of {self.user.username}"


class MfaRecoveryCode(models.Model):
    """One-use code for when the phone is lost or not at hand (DECISIONS A5). Only a SHA-256 is stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa_recovery_codes")
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Recovery code of {self.user.username}"


# --- Delegation -------------------------------------------------------------


class Delegate(models.Model):
    """Time-bound delegation for Executive approvals (e.g. Mayor's representative)."""

    principal = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delegations_given"
    )
    delegate = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delegations_received"
    )
    valid_from = models.DateField()
    valid_to = models.DateField()
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-valid_from"]
        permissions = [
            ("manage_delegates", "Can assign and manage executive delegates"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(valid_to__gte=F("valid_from")),
                name="delegate_valid_to_gte_from",
                violation_error_message="Delegation end date cannot be before start date.",
            ),
        ]

    def __str__(self):
        return f"{self.delegate.username} for {self.principal.username} ({self.valid_from} to {self.valid_to})"

    def is_active(self, target_date=None):
        if target_date is None:
            from django.utils import timezone
            target_date = timezone.localdate()
        return self.valid_from <= target_date <= self.valid_to


# --- Audit Log ---------------------------------------------------------------


class AuditLog(models.Model):
    """Append-only audit trail capturing model mutations, security events, and record views.

    Design constraints (DECISIONS U1-U6):
    - No foreign keys to User or Office: stores plain user_id and snapshots so users can be deleted.
    - Protected by PostgreSQL database trigger preventing UPDATE, DELETE, and TRUNCATE.
    - Contains optional SHA-256 hash chaining (prev_hash, row_hash).
    """

    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        VIEW = "view", "View"
        EXPORT = "export", "Export"
        LOGIN = "login", "Login"
        LOGOUT = "logout", "Logout"
        FAILED_LOGIN = "failed_login", "Failed Login"
        ACCOUNT_LOCKOUT = "lockout", "Account Lockout"
        PASSWORD_RESET = "password_reset", "Password Reset"
        GROUP_CHANGE = "group_change", "Group Change"
        PERMISSION_DENIED = "permission_denied", "Permission Denied (403)"
        VERIFY_CHAIN = "verify_chain", "Audit Chain Verification"
        MFA_SETUP = "mfa_setup", "Two-Step Login Set Up"
        MFA_PASSED = "mfa_passed", "Two-Step Login Passed"
        MFA_FAILED = "mfa_failed", "Two-Step Login Failed"
        MFA_RESET = "mfa_reset", "Two-Step Login Reset"

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    action = models.CharField(max_length=30, choices=Action.choices, db_index=True)

    # Plain integer IDs and snapshots without foreign keys (Requirement 2)
    user_id = models.IntegerField(null=True, blank=True, db_index=True)
    username = models.CharField(max_length=150, blank=True, db_index=True)
    office_id = models.IntegerField(null=True, blank=True, db_index=True)
    office_name = models.CharField(max_length=200, blank=True)

    # Target entity
    model_name = models.CharField(max_length=100, blank=True, db_index=True)
    object_id = models.CharField(max_length=100, blank=True, db_index=True)
    object_repr = models.CharField(max_length=255, blank=True)

    # Changes diff (field names only for sensitive fields, no values or password hashes)
    changes = models.JSONField(default=dict, blank=True)

    # Network, session, and request tracing metadata (Requirements 3 & 4)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    session_key_hash = models.CharField(max_length=64, blank=True, db_index=True)

    # Cryptographic SHA-256 hash chain
    prev_hash = models.CharField(max_length=64, blank=True)
    row_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "hr_auditlog"
        ordering = ["-timestamp", "-id"]
        indexes = [
            models.Index(fields=["model_name", "object_id"], name="audit_target_idx"),
            models.Index(fields=["action", "timestamp"], name="audit_act_time_idx"),
        ]
        permissions = [
            ("view_audit_log", "Can view and export system audit logs"),
        ]

    def __str__(self):
        user_str = self.username or (f"User #{self.user_id}" if self.user_id else "System")
        return f"[{self.timestamp:%Y-%m-%d %H:%M:%S}] {user_str} {self.action} {self.model_name}:{self.object_id}"

    def calculate_row_hash(self, prev_hash=None):
        """Calculates canonical SHA-256 hash of this audit row with its previous hash link."""
        if prev_hash is None:
            prev_hash = self.prev_hash
        ts_str = self.timestamp.isoformat() if self.timestamp else ""
        canonical_changes = json.dumps(self.changes or {}, sort_keys=True)
        parts = [
            str(prev_hash or ""),
            ts_str,
            str(self.action or ""),
            str(self.user_id if self.user_id is not None else ""),
            str(self.username or ""),
            str(self.office_id if self.office_id is not None else ""),
            str(self.office_name or ""),
            str(self.model_name or ""),
            str(self.object_id or ""),
            str(self.object_repr or ""),
            canonical_changes,
            str(self.ip_address or ""),
            str(self.request_id or ""),
            str(self.session_key_hash or ""),
        ]
        payload = "|".join(parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditChainStatus(models.Model):
    """Result of the most recent full audit hash-chain verification (a single row, pk=1).

    Written by the verify_audit_chain command and by the rate-limited "Re-verify all"
    action on /audit/, so the page can show the last result without scanning the chain.
    Not itself audited; each web re-verification is logged as AuditLog.Action.VERIFY_CHAIN.
    """

    class AnchorStatus(models.TextChoices):
        NONE = "none", "No anchor supplied"
        VERIFIED = "verified", "Anchor verified"
        NOT_VERIFIED = "not_verified", "Anchor not verified"

    verified_at = models.DateTimeField()
    is_valid = models.BooleanField()
    record_count = models.PositiveIntegerField()
    last_record_id = models.IntegerField(null=True, blank=True)
    broken_id = models.IntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    anchor = models.CharField(max_length=100, blank=True)
    anchor_status = models.CharField(max_length=20, choices=AnchorStatus.choices, default=AnchorStatus.NONE)
    source = models.CharField(max_length=20, help_text="'command' or 'web'")
    verified_by = models.CharField(max_length=150, blank=True)

    class Meta:
        verbose_name_plural = "audit chain status"

    def __str__(self):
        return f"{'OK' if self.is_valid else 'BROKEN'} at {self.verified_at:%Y-%m-%d %H:%M:%S} ({self.record_count} records)"
