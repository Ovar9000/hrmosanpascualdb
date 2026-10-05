"""Forms for San Pascual HR records data entry and editing."""

import re
from datetime import date
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from .models import FAKE_ID_FIELDS, Person
from .permissions import can_view_ids


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
