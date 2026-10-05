"""Forms for San Pascual HR records data entry and editing."""

import re
from datetime import date
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from .models import FAKE_ID_FIELDS, Engagement, Person
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

        # Default leave_eligible based on engagement type if new form
        if not self.instance.pk and "leave_eligible" in self.fields and not self.initial.get("leave_eligible"):
            initial_type = self.initial.get("type")
            if initial_type in [Engagement.Type.PLANTILLA, Engagement.Type.CASUAL]:
                self.fields["leave_eligible"].initial = True

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
