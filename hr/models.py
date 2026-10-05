"""HR data model (first draft).

Kept deliberately flat and easy to change after the HRMO survey (see DECISIONS.md):
option lists are TextChoices in this file; most business rules are DB CHECK
constraints, which Django also validates in forms and the admin.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

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
    location = models.CharField(max_length=200, blank=True)
    contact = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# --- People ------------------------------------------------------------------

# Prototype guard (DECISIONS D1): government ID fields only accept obviously fake
# values starting with "00". Remove these constraints before go-live.
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

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name_plural = "people"
        constraints = [
            # DECISIONS X2: same last name + first name + birthdate = same person.
            models.UniqueConstraint(
                Lower("last_name"), Lower("first_name"), "birthdate",
                name="person_unique_name_birthdate",
                violation_error_message="A person with this name and birthdate already exists.",
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
        """DECISIONS L3: only active Plantilla and Casual engagements earn leave credits."""
        return self.engagements.filter(
            status=Engagement.Status.ACTIVE,
            type__in=Engagement.LEAVE_CREDIT_TYPES,
        ).exists()


# --- Projects ----------------------------------------------------------------


class Project(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ONGOING = "ongoing", "Ongoing"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=200)
    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name="projects")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="project_end_after_start",
                violation_error_message="End date cannot be before start date.",
            ),
        ]

    def __str__(self):
        return self.name


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

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="engagements")
    office = models.ForeignKey(Office, on_delete=models.PROTECT, related_name="engagements")
    type = models.CharField(max_length=20, choices=Type.choices)
    position_title = models.CharField(max_length=150)
    # Payroll *input* only (DECISIONS D2). Visible to HRMO and auditors only (R6).
    rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rate_basis = models.CharField(max_length=20, choices=RateBasis.choices, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    # Plantilla only (DECISIONS D5).
    salary_grade = models.PositiveSmallIntegerField(null=True, blank=True)
    step = models.PositiveSmallIntegerField(null=True, blank=True)
    item_number = models.CharField(max_length=50, blank=True)

    # Project-based only.
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.PROTECT, related_name="engagements"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date"]
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


# --- Documents (kept simple until the survey, DECISIONS F1) ------------------


def validate_file_size(f):
    if f.size > 10 * 1024 * 1024:
        raise ValidationError("File too large (max 10 MB).")


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
        upload_to="documents/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"]), validate_file_size],
    )
    physical_location = models.CharField(
        max_length=100, blank=True, help_text="e.g. Cabinet 3, Drawer 2"
    )
    notes = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-uploaded_at"]
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


# --- Leave (kept simple until the survey, DECISIONS L1-L3) -------------------


class LeaveType(models.TextChoices):
    VACATION = "vl", "Vacation leave"
    SICK = "sl", "Sick leave"
    SPECIAL_PRIVILEGE = "spl", "Special privilege leave"
    MANDATORY = "fl", "Mandatory/forced leave"
    MATERNITY = "maternity", "Maternity leave"
    PATERNITY = "paternity", "Paternity leave"
    SOLO_PARENT = "solo_parent", "Solo parent leave"
    OTHER = "other", "Other"


class LeaveRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    # Entered by the employee, not computed (weekends/holidays rules TBD by survey).
    days = models.DecimalField("Working days", max_digits=5, decimal_places=1)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    filed_at = models.DateTimeField(auto_now_add=True)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-filed_at"]
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
            raise ValidationError("Only people with an active Plantilla or Casual engagement have leave credits.")


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

    def __str__(self):
        return f"Profile of {self.user.username}"
