import os
import re
from datetime import date
from pathlib import Path
from PIL import Image
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from .models import FAKE_ID_FIELDS, CalendarEvent, Document, Engagement, Person, Project, ServiceInterruption
from .permissions import can_view_ids, can_view_pay


class PersonForm(forms.ModelForm):
    """Data entry and edit form for personnel records with concurrency versioning and security gates."""

    version = forms.IntegerField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Person
        fields = [
            "last_name",
            "first_name",
            "middle_name",
            "suffix",
            "sex",
            "birthdate",
            "mobile",
            "email",
            "address",
            "tin",
            "gsis_no",
            "philhealth_no",
            "pagibig_no",
            "sss_no",
        ]
        widgets = {
            "birthdate": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Dela Cruz"}),
            "first_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Juan"}),
            "middle_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Santos"}),
            "suffix": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Jr., Sr., III"}),
            "sex": forms.Select(attrs={"class": "form-select"}),
            "mobile": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. 0917-123-4567"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "e.g. juan.delacruz@sanpascual.gov.ph"}),
            "address": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Poblacion, San Pascual, Batangas"}),
            "tin": forms.TextInput(attrs={"class": "form-control", "placeholder": "000-000-000-000"}),
            "gsis_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "0000000000"}),
            "philhealth_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "00-000000000-0"}),
            "pagibig_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "0000-0000-0000"}),
            "sss_no": forms.TextInput(attrs={"class": "form-control", "placeholder": "00-0000000-0"}),
        }
        labels = {
            "tin": "TIN",
            "gsis_no": "GSIS BP Number",
            "philhealth_no": "PhilHealth Number",
            "pagibig_no": "Pag-IBIG MID Number",
            "sss_no": "SSS Number",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        # Set initial version for edit forms
        if self.instance and self.instance.pk:
            self.fields["version"].initial = self.instance.version

        # Field-level security gate: ID fields only for users with view_ids (DECISIONS R4/U4)
        if not can_view_ids(user):
            for f in FAKE_ID_FIELDS:
                if f in self.fields:
                    del self.fields[f]

    def clean_first_name(self):
        return self.cleaned_data.get("first_name", "").strip()

    def clean_last_name(self):
        return self.cleaned_data.get("last_name", "").strip()

    def clean_middle_name(self):
        return self.cleaned_data.get("middle_name", "").strip()

    def clean_suffix(self):
        return self.cleaned_data.get("suffix", "").strip()

    def clean_mobile(self):
        return self.cleaned_data.get("mobile", "").strip()

    def clean_address(self):
        return self.cleaned_data.get("address", "").strip()

    def clean_birthdate(self):
        bdate = self.cleaned_data.get("birthdate")
        if not bdate:
            return bdate
        today = date.today()
        if bdate > today:
            raise ValidationError("Birthdate cannot be in the future.")

        # Age calculation in full years
        age = today.year - bdate.year - ((today.month, today.day) < (bdate.month, bdate.day))
        min_age = getattr(settings, "MIN_EMPLOYEE_AGE", 18)
        max_age = getattr(settings, "MAX_EMPLOYEE_AGE", 90)

        if age < min_age:
            raise ValidationError(f"Employee must be at least {min_age} years old (current age: {age}).")
        if age > max_age:
            raise ValidationError(f"Employee age cannot exceed {max_age} years (current age: {age}).")
        return bdate

    def clean(self):
        cleaned_data = super().clean()
        last_name = cleaned_data.get("last_name")
        first_name = cleaned_data.get("first_name")
        middle_name = cleaned_data.get("middle_name", "")
        suffix = cleaned_data.get("suffix", "")
        birthdate = cleaned_data.get("birthdate")

        # Duplicate person detection considering middle name and suffix (DECISIONS S5)
        if last_name and first_name and birthdate:
            dup_qs = Person.objects.filter(
                last_name__iexact=last_name,
                first_name__iexact=first_name,
                middle_name__iexact=middle_name,
                suffix__iexact=suffix,
                birthdate=birthdate,
            )
            if self.instance and self.instance.pk:
                dup_qs = dup_qs.exclude(pk=self.instance.pk)

            dup_person = dup_qs.first()
            if dup_person:
                raise ValidationError(
                    f"A person with this full name ({last_name}, {first_name} {middle_name} {suffix}) "
                    f"and birthdate ({birthdate}) already exists (Employee #{dup_person.employee_no})."
                )

        # Prototype fake ID validation: must start with 00 (DECISIONS D1)
        if getattr(settings, "PROTOTYPE_FAKE_DATA_ONLY", True):
            for f in FAKE_ID_FIELDS:
                val = cleaned_data.get(f)
                if val and not re.match(r"^00", val):
                    self.add_error(f, "Prototype accepts fake ID numbers only (must start with 00).")

        return cleaned_data


class EngagementForm(forms.ModelForm):
    """Form for creating and editing personnel engagements with permission gates and overlap checks."""

    version = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    confirm_overlap = forms.BooleanField(
        required=False,
        label="I understand that an active engagement of the same type in this office overlaps these dates, save anyway",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = Engagement
        fields = [
            "office",
            "type",
            "position_title",
            "reports_to",
            "rate",
            "rate_basis",
            "start_date",
            "end_date",
            "status",
            "leave_eligible",
            "salary_grade",
            "step",
            "item_number",
            "project",
            "notes",
        ]
        widgets = {
            "office": forms.Select(attrs={"class": "form-select"}),
            "type": forms.Select(attrs={"class": "form-select"}),
            "position_title": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Administrative Officer IV"}),
            "reports_to": forms.Select(attrs={"class": "form-select"}),
            "rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "0.00"}),
            "rate_basis": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "leave_eligible": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "salary_grade": forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "33"}),
            "step": forms.NumberInput(attrs={"class": "form-control", "min": "1", "max": "8"}),
            "item_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. SP-MO-001"}),
            "project": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, user=None, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.person = person

        # Pre-populate version for edit
        if self.instance and self.instance.pk:
            self.fields["version"].initial = self.instance.version
            if not self.person:
                self.person = self.instance.person

        # Type-based leave_eligible default is only an initial value (DECISIONS L3); HRMO may untick it.
        if not self.instance.pk and "leave_eligible" in self.fields and "leave_eligible" not in self.initial:
            self.fields["leave_eligible"].initial = Engagement.default_leave_eligible(self.initial.get("type"))

        # Only projects open for hiring (planned/ongoing, funding reference recorded) can take new
        # contracts. An engagement already on a closed project keeps it when edited (DECISIONS P2/P3).
        if "project" in self.fields:
            open_projects = Q(status__in=Project.HIRING_STATUSES) & ~Q(funding_reference="")
            if self.instance.pk and self.instance.project_id:
                open_projects |= Q(pk=self.instance.project_id)
            self.fields["project"].queryset = Project.objects.filter(open_projects).select_related("office")

        # Reporting line for the org chart: active chart-type appointments only (DECISIONS O2).
        # clean() also requires the same office, since the office can change on this form.
        if "reports_to" in self.fields:
            supervisors = Engagement.objects.filter(
                status=Engagement.Status.ACTIVE, type__in=Engagement.CHART_TYPES
            ).select_related("person", "office").order_by("office__name", "person__last_name")
            if self.instance.pk:
                supervisors = supervisors.exclude(pk=self.instance.pk)
            field = self.fields["reports_to"]
            field.queryset = supervisors
            field.empty_label = "— Nobody in this office (top of the office chart) —"
            field.label_from_instance = lambda e: (
                f"{e.office.acronym or e.office.name} · {e.person.full_name} — {e.position_title}"
            )
            field.help_text = "Their supervisor in the same office. Leave empty for the office head."

        # Field-level permission gate: pay rate fields visible only with view_pay (DECISIONS R6)
        from .permissions import can_view_pay
        if not can_view_pay(user):
            if "rate" in self.fields:
                del self.fields["rate"]
            if "rate_basis" in self.fields:
                del self.fields["rate_basis"]

    def clean_position_title(self):
        return self.cleaned_data.get("position_title", "").strip()

    def clean_item_number(self):
        return self.cleaned_data.get("item_number", "").strip()

    def clean(self):
        cleaned_data = super().clean()
        eng_type = cleaned_data.get("type")
        salary_grade = cleaned_data.get("salary_grade")
        step = cleaned_data.get("step")
        item_number = cleaned_data.get("item_number", "")
        project = cleaned_data.get("project")
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        office = cleaned_data.get("office")
        status = cleaned_data.get("status")

        # Plantilla validations
        if eng_type == Engagement.Type.PLANTILLA:
            if not salary_grade or not step or not item_number:
                raise ValidationError("Salary grade, step, and item number are required for Plantilla appointments.")
            if salary_grade < 1 or salary_grade > 33:
                self.add_error("salary_grade", "Salary grade must be between 1 and 33.")
            if step < 1 or step > 8:
                self.add_error("step", "Step must be between 1 and 8.")
        else:
            if salary_grade or step or item_number:
                raise ValidationError("Salary grade, step, and item number must be empty for non-Plantilla engagements.")

        # Project-based validations
        if eng_type == Engagement.Type.PROJECT_BASED:
            if not project:
                raise ValidationError("Project-based engagements require an associated project.")
        else:
            if project:
                raise ValidationError("Only project-based engagements can link to a project.")

        # Date chronological order
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "End date cannot be earlier than start date.")

        # A project-based contract must fit inside its project's dates. Extending a contract past
        # the project end needs the Project Maintainer to extend the project first (DECISIONS P3).
        if project and eng_type == Engagement.Type.PROJECT_BASED and start_date:
            period = f"{project.start_date:%b %d, %Y} – " + (
                f"{project.end_date:%b %d, %Y}" if project.end_date else "open-ended"
            )
            if start_date < project.start_date:
                self.add_error("start_date", f"Contract cannot start before the project does ({period}).")
            if project.end_date and (end_date is None or end_date > project.end_date):
                self.add_error(
                    "end_date",
                    f"Contract must end by the project end date ({period}). "
                    "Ask the Project Maintainer to extend the project first.",
                )

        supervisor = cleaned_data.get("reports_to")
        if supervisor is not None:
            from .orgchart import supervisor_error

            message = supervisor_error(self.instance.pk, office, supervisor)
            if message:
                self.add_error("reports_to", message)

        # Overlap check with existing active engagements of same type in same office
        if self.person and office and eng_type and start_date and status == Engagement.Status.ACTIVE:
            overlap_qs = Engagement.objects.filter(
                person=self.person,
                office=office,
                type=eng_type,
                status=Engagement.Status.ACTIVE,
            )
            if self.instance and self.instance.pk:
                overlap_qs = overlap_qs.exclude(pk=self.instance.pk)

            has_overlap = False
            for other in overlap_qs:
                other_end = other.end_date or date.max
                this_end = end_date or date.max
                if start_date <= other_end and other.start_date <= this_end:
                    has_overlap = True
                    break

            if has_overlap and not cleaned_data.get("confirm_overlap"):
                self.add_error(
                    "confirm_overlap",
                    f"Warning: {self.person.full_name} already has an active {eng_type} engagement in {office.name} "
                    "overlapping these dates. Check 'I understand, save anyway' to proceed."
                )

        return cleaned_data


class EndEngagementForm(forms.Form):
    """Confirmation form for ending / retiring an engagement."""

    version = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    end_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        label="Contract / Term End Date",
        required=True,
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        label="Reason / Notes",
        required=False,
    )

    def __init__(self, *args, engagement=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.engagement = engagement
        if engagement:
            self.fields["version"].initial = engagement.version
            if engagement.end_date:
                self.fields["end_date"].initial = engagement.end_date
            else:
                self.fields["end_date"].initial = date.today()

    def clean_end_date(self):
        ed = self.cleaned_data.get("end_date")
        if self.engagement and ed and ed < self.engagement.start_date:
            raise ValidationError("End date cannot be earlier than the start date of the appointment.")
        return ed


class ProjectForm(forms.ModelForm):
    """Create / edit an LGU project (Project Maintainer only, DECISIONS P1-P3)."""

    version = forms.IntegerField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Project
        fields = ["name", "office", "status", "start_date", "end_date", "funding_reference", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Coastal Road Rehabilitation"}),
            "office": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "funding_reference": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. CAF-2026-0142"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["version"].initial = self.instance.version
        # Required on the form; the column stays blank-able for projects recorded before P2.
        self.fields["funding_reference"].required = True

    def clean_name(self):
        return self.cleaned_data.get("name", "").strip()

    def clean_funding_reference(self):
        return self.cleaned_data.get("funding_reference", "").strip()

    def clean(self):
        cleaned_data = super().clean()
        end_date = cleaned_data.get("end_date")
        start_date = cleaned_data.get("start_date")
        if not self.instance.pk:
            return cleaned_data

        # Moving the project's dates must not strand contracts already hired on it (P3).
        active = self.instance.engagements.filter(status=Engagement.Status.ACTIVE)
        if end_date:
            outside = active.filter(Q(end_date__isnull=True) | Q(end_date__gt=end_date)).count()
            if outside:
                self.add_error(
                    "end_date",
                    f"{outside} active contract(s) on this project run past this date. "
                    "HRMO must end or shorten them first.",
                )
        if start_date:
            early = active.filter(start_date__lt=start_date).count()
            if early:
                self.add_error(
                    "start_date",
                    f"{early} active contract(s) on this project start before this date.",
                )
        return cleaned_data


def sanitize_filename(filename):
    """Sanitize user-provided filename for safe database display name."""
    if not filename:
        return "document"
    base = Path(filename).name
    base = base.replace("\x00", "").replace("/", "").replace("\\", "")
    cleaned = re.sub(r"[^\w\s\.\-\(\)]", "_", base).strip()
    return cleaned[:255] or "document"


def validate_uploaded_document_file(uploaded_file):
    """Validate uploaded document file size, extension, magic bytes, and image integrity."""
    if not uploaded_file:
        return

    max_size = getattr(settings, "MAX_UPLOAD_SIZE", 5 * 1024 * 1024)
    if uploaded_file.size > max_size:
        raise ValidationError(f"File exceeds maximum upload size of {max_size // (1024 * 1024)} MB.")

    ext = os.path.splitext(uploaded_file.name)[1].lower()
    allowed_exts = [".pdf", ".jpg", ".jpeg", ".png"]
    if ext not in allowed_exts:
        raise ValidationError(f"Unsupported file format '{ext}'. Only PDF, JPG, and PNG files are allowed.")

    pos = uploaded_file.tell() if hasattr(uploaded_file, "tell") else 0
    header = uploaded_file.read(32)
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(pos)

    if ext == ".pdf":
        if not header.startswith(b"%PDF"):
            raise ValidationError("File content is not a valid PDF document (invalid magic bytes signature).")
    elif ext in [".jpg", ".jpeg"]:
        if not header.startswith(b"\xff\xd8\xff"):
            raise ValidationError("File content is not a valid JPEG image (invalid magic bytes signature).")
        try:
            img = Image.open(uploaded_file)
            img.verify()
        except Exception:
            raise ValidationError("Uploaded JPEG image is corrupted or invalid.")
        finally:
            if hasattr(uploaded_file, "seek"):
                uploaded_file.seek(pos)
    elif ext == ".png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValidationError("File content is not a valid PNG image (invalid magic bytes signature).")
        try:
            img = Image.open(uploaded_file)
            img.verify()
        except Exception:
            raise ValidationError("Uploaded PNG image is corrupted or invalid.")
        finally:
            if hasattr(uploaded_file, "seek"):
                uploaded_file.seek(pos)


class DocumentForm(forms.ModelForm):
    """Form for uploading or recording a physical/digital document for a Person or Engagement."""

    version = forms.IntegerField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Document
        fields = [
            "doc_type",
            "status",
            "file",
            "original_on_file",
            "physical_location",
            "notes",
            "verified_by",
        ]
        widgets = {
            "doc_type": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "file": forms.FileInput(attrs={"class": "form-control", "accept": ".pdf,.jpg,.jpeg,.png"}),
            "original_on_file": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "physical_location": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Cabinet 2, Drawer 1"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Additional notes..."}),
            "verified_by": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, person=None, engagement=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person
        self.engagement = engagement
        if self.instance and self.instance.pk:
            self.fields["version"].initial = self.instance.version

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if f:
            validate_uploaded_document_file(f)
        return f

    def save(self, commit=True):
        doc = super().save(commit=False)
        if self.person:
            doc.person = self.person
        if self.engagement:
            doc.engagement = self.engagement
        f = self.cleaned_data.get("file")
        if f and hasattr(f, "name"):
            doc.display_name = sanitize_filename(f.name)
        if commit:
            doc.save()
        return doc


class DocumentEditForm(forms.ModelForm):
    """Form for editing document metadata without altering existing uploaded scans."""

    version = forms.IntegerField(widget=forms.HiddenInput(), required=False)

    class Meta:
        model = Document
        fields = [
            "doc_type",
            "status",
            "original_on_file",
            "physical_location",
            "notes",
            "verified_by",
        ]
        widgets = {
            "doc_type": forms.Select(attrs={"class": "form-select"}),
            "status": forms.Select(attrs={"class": "form-select"}),
            "original_on_file": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "physical_location": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "verified_by": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["version"].initial = self.instance.version


class DocumentReplaceForm(forms.Form):
    """Form for uploading a replacement scan of an existing document without overwriting historical records."""

    version = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    file = forms.FileField(
        required=True,
        widget=forms.FileInput(attrs={"class": "form-control", "accept": ".pdf,.jpg,.jpeg,.png"}),
        label="New Scan File (PDF, JPG, PNG - max 5 MB)",
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Reason for re-scan / replacement..."}),
        label="Replacement Reason / Notes",
        required=False,
    )
    original_on_file = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
        label="Original paper copy on file",
    )
    physical_location = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        label="Physical Location",
    )

    def __init__(self, *args, document=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.document = document
        if document:
            self.fields["version"].initial = document.version
            self.fields["original_on_file"].initial = document.original_on_file
            self.fields["physical_location"].initial = document.physical_location

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if f:
            validate_uploaded_document_file(f)
        return f



class CalendarEventForm(forms.ModelForm):
    """Create / edit a calendar event: interview, deadline, holiday, meeting (HRMO, DECISIONS C1)."""

    class Meta:
        model = CalendarEvent
        fields = ["title", "kind", "start_date", "end_date", "time", "office", "notes"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Interview: Admin Aide I applicants"}),
            "kind": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"),
            "time": forms.TimeInput(attrs={"type": "time", "class": "form-control"}, format="%H:%M"),
            "office": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
        labels = {"start_date": "Date", "end_date": "Until (optional)", "office": "Office"}


class ServiceInterruptionForm(forms.ModelForm):
    """Leave without pay or suspension, recorded by HRMO from a salary action case (SA2)."""

    class Meta:
        model = ServiceInterruption
        fields = ["kind", "start_date", "end_date", "notes"]
        widgets = {
            "kind": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "form-control form-control-sm"}),
            "notes": forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "e.g. LWOP per leave form no."}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before start date.")
        return cleaned
