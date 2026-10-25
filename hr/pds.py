"""Personal Data Sheet (CS Form No. 212 Revised 2017): sections, the frozen copy HRMO verifies,
and which parts are still to fill in (DECISIONS PD1-PD7)."""

import re
from datetime import date
from decimal import Decimal

from .certificates import excluded_types, status_text
from .models import (
    Engagement, PdsChild, PdsEducation, PdsEligibility, PdsProfile, PdsReference, PdsSubmission, PdsTraining,
    PdsVoluntaryWork, PdsWorkExperience,
)

PERSONAL_FIELDS = [
    "place_of_birth", "civil_status", "civil_status_other", "height_m", "weight_kg", "blood_type", "citizenship",
    "dual_by", "dual_country", "res_house", "res_street", "res_subdivision", "res_barangay", "res_city",
    "res_province", "res_zip", "perm_same", "perm_house", "perm_street", "perm_subdivision", "perm_barangay",
    "perm_city", "perm_province", "perm_zip", "telephone",
]
FAMILY_FIELDS = [
    "spouse_surname", "spouse_first_name", "spouse_middle_name", "spouse_extension", "spouse_occupation",
    "spouse_employer", "spouse_business_address", "spouse_telephone", "father_surname", "father_first_name",
    "father_middle_name", "father_extension", "mother_surname", "mother_first_name", "mother_middle_name",
]
OTHER_FIELDS = ["skills", "distinctions", "memberships"]
QUESTION_ANSWERS = ["q34a", "q34b", "q35a", "q35b", "q36", "q37", "q38a", "q38b", "q39", "q40a", "q40b", "q40c"]
QUESTION_FIELDS = [
    "q34a", "q34b", "q34_details", "q35a", "q35a_details", "q35b", "q35b_details", "q35b_date_filed", "q35b_status",
    "q36", "q36_details", "q37", "q37_details", "q38a", "q38a_details", "q38b", "q38b_details", "q39", "q39_details",
    "q40a", "q40a_details", "q40b", "q40b_details", "q40c", "q40c_details",
]
GOV_ID_FIELDS = ["gov_id_type", "gov_id_number", "gov_id_issued"]
PROFILE_FIELDS = PERSONAL_FIELDS + FAMILY_FIELDS + OTHER_FIELDS + QUESTION_FIELDS + GOV_ID_FIELDS

LISTS = {
    "children": (PdsChild, ["full_name", "birthdate"]),
    "education": (PdsEducation, ["level", "school", "degree", "year_from", "year_to", "units_earned",
                                 "year_graduated", "honors"]),
    "eligibility": (PdsEligibility, ["name", "rating", "exam_date", "exam_place", "license_number",
                                     "license_valid_until"]),
    "work": (PdsWorkExperience, ["date_from", "date_to", "position", "employer", "monthly_salary", "grade_step",
                                 "appointment_status", "government"]),
    "voluntary": (PdsVoluntaryWork, ["organization", "date_from", "date_to", "hours", "position"]),
    "training": (PdsTraining, ["title", "date_from", "date_to", "hours", "kind", "sponsor"]),
    "references": (PdsReference, ["name", "address", "telephone"]),
}

# (key, title, required to submit, profile fields, lists)
SECTIONS = [
    ("personal", "I. Personal information", True, PERSONAL_FIELDS, []),
    ("family", "II. Family background", True, FAMILY_FIELDS, ["children"]),
    ("education", "III. Educational background", True, [], ["education"]),
    ("eligibility", "IV. Civil service eligibility", False, [], ["eligibility"]),
    ("work", "V. Work experience", False, [], ["work"]),
    ("voluntary", "VI. Voluntary work", False, [], ["voluntary"]),
    ("training", "VII. Learning and development", False, [], ["training"]),
    ("other", "VIII. Other information", False, OTHER_FIELDS, []),
    ("questions", "Questions 34 to 40", True, QUESTION_FIELDS, []),
    ("references", "References and government ID", True, GOV_ID_FIELDS, ["references"]),
]
SECTION_KEYS = [s[0] for s in SECTIONS]
TITLES = {s[0]: s[1] for s in SECTIONS}

# The questions as printed on the form, with the answer and details fields that go with each.
QUESTIONS = [
    ("34", "Are you related by consanguinity or affinity to the appointing or recommending authority, or to the "
           "chief of bureau or office or to the person who has immediate supervision over you in the Office, Bureau "
           "or Department where you will be appointed,", [
        ("q34a", "a. within the third degree?", None),
        ("q34b", "b. within the fourth degree (for Local Government Unit - Career Employees)?", "q34_details"),
    ]),
    ("35", "", [
        ("q35a", "a. Have you ever been found guilty of any administrative offense?", "q35a_details"),
        ("q35b", "b. Have you been criminally charged before any court?", "q35b_details"),
    ]),
    ("36", "", [("q36", "Have you ever been convicted of any crime or violation of any law, decree, ordinance or "
                        "regulation by any court or tribunal?", "q36_details")]),
    ("37", "", [("q37", "Have you ever been separated from the service in any of the following modes: resignation, "
                        "retirement, dropped from the rolls, dismissal, termination, end of term, finished contract or "
                        "phased out (abolition) in the public or private sector?", "q37_details")]),
    ("38", "", [
        ("q38a", "a. Have you ever been a candidate in a national or local election held within the last year "
                 "(except Barangay election)?", "q38a_details"),
        ("q38b", "b. Have you resigned from the government service during the three (3)-month period before the last "
                 "election to promote/actively campaign for a national or local candidate?", "q38b_details"),
    ]),
    ("39", "", [("q39", "Have you acquired the status of an immigrant or permanent resident of another country?",
                 "q39_details")]),
    ("40", "Pursuant to: (a) Indigenous People's Act (RA 8371); (b) Magna Carta for Disabled Persons (RA 7277); and "
           "(c) Solo Parents Welfare Act of 2000 (RA 8972), please answer the following items:", [
        ("q40a", "a. Are you a member of any indigenous group?", "q40a_details"),
        ("q40b", "b. Are you a person with disability?", "q40b_details"),
        ("q40c", "c. Are you a solo parent?", "q40c_details"),
    ]),
]
DETAIL_LABELS = {"q39_details": "Country", "q40a_details": "Please specify", "q40b_details": "PWD ID no.",
                 "q40c_details": "Solo parent ID no."}

DATE_KEYS = {"birthdate", "exam_date", "license_valid_until", "date_from", "date_to", "q35b_date_filed"}
LABELS = {
    "civil_status": dict(PdsProfile.CivilStatus.choices),
    "citizenship": dict(PdsProfile.Citizenship.choices),
    "dual_by": dict(PdsProfile.DualBy.choices),
    "level": dict(PdsEducation.Level.choices),
    "kind": dict(PdsTraining.Kind.choices),
}


def _ser(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def profile_for(person, create=False):
    if create:
        return PdsProfile.objects.get_or_create(person=person)[0]
    return PdsProfile.objects.filter(person=person).first() or PdsProfile(person=person)


def snapshot(person):
    """Everything on the PDS as plain JSON values: what HRMO verifies and keeps (PD4)."""
    profile = profile_for(person)
    data = {
        "person": {
            "employee_no": person.employee_no, "last_name": person.last_name, "first_name": person.first_name,
            "middle_name": person.middle_name, "suffix": person.suffix, "sex": person.sex,
            "birthdate": _ser(person.birthdate), "mobile": person.mobile, "email": person.email,
            "tin": person.tin, "gsis_no": person.gsis_no, "philhealth_no": person.philhealth_no,
            "pagibig_no": person.pagibig_no, "sss_no": person.sss_no,
        },
        "profile": {f: _ser(getattr(profile, f)) for f in PROFILE_FIELDS},
    }
    for key, (model, fields) in LISTS.items():
        data[key] = [{f: _ser(getattr(row, f)) for f in fields} for row in model.objects.filter(person=person)]
    return data


def _section_data(snap, key):
    _key, _title, _required, fields, lists = next(s for s in SECTIONS if s[0] == key)
    profile = snap.get("profile", {})
    return {"fields": {f: profile.get(f) for f in fields}, **{name: snap.get(name, []) for name in lists}}


def changed_sections(old, new):
    """Section keys that differ between two snapshots. Person fields are HRMO's and not compared."""
    if old is None:
        return set(SECTION_KEYS)
    return {key for key in SECTION_KEYS if _section_data(old, key) != _section_data(new, key)}


def completeness(snap):
    """Whether each section required to submit is filled in, and how many entries each list has (PD2)."""
    p = snap["profile"]
    done = {
        "personal": bool(p["place_of_birth"] and p["civil_status"] and p["res_barangay"] and p["res_city"]),
        "family": bool(p["father_surname"] and p["mother_surname"]
                       and (p["civil_status"] != PdsProfile.CivilStatus.MARRIED or p["spouse_surname"])),
        "education": bool(snap["education"]),
        "questions": all(p[q] is not None for q in QUESTION_ANSWERS),
        "references": len(snap["references"]) >= 3 and bool(p["gov_id_type"] and p["gov_id_number"]),
    }
    rows = []
    for key, title, required, _fields, lists in SECTIONS:
        count = sum(len(snap[name]) for name in lists) if lists else None
        rows.append({"key": key, "title": title, "required": required, "done": done.get(key, True),
                     "count": count})
    return rows


def missing(snap):
    return [r for r in completeness(snap) if r["required"] and not r["done"]]


def state(person, snap=None):
    """Where the person's PDS stands: the waiting or last verified copy, and unsent changes."""
    snap = snap or snapshot(person)
    submissions = PdsSubmission.objects.filter(person=person)
    waiting = submissions.filter(status=PdsSubmission.Status.SUBMITTED).first()
    verified = submissions.filter(status=PdsSubmission.Status.VERIFIED).first()
    latest = submissions.first()
    against = waiting or verified
    return {
        "snap": snap,
        "waiting": waiting,
        "verified": verified,
        "latest": latest,
        "returned": latest if latest and latest.status == PdsSubmission.Status.RETURNED else None,
        "unsent": changed_sections(against.snapshot if against else None, snap) if against else set(),
        "never": latest is None,
        "missing": missing(snap),
    }


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_row(row):
    out = {}
    for key, value in row.items():
        if key in DATE_KEYS and isinstance(value, str) and _ISO.match(value):
            value = date.fromisoformat(value)
        out[key] = value
        if key in LABELS and value:
            out[f"{key}_label"] = LABELS[key].get(value, value)
    return out


def display(snap):
    """The snapshot with dates as dates and choice labels added, for templates."""
    loaded = {"person": _load_row(snap["person"]), "profile": _load_row(snap["profile"])}
    for key in LISTS:
        loaded[key] = [_load_row(r) for r in snap.get(key, [])]
    profile = loaded["profile"]
    for name in OTHER_FIELDS:
        profile[f"{name}_lines"] = [line.strip() for line in (profile.get(name) or "").splitlines() if line.strip()]
    return loaded


def lgu_work_rows(person, show_pay):
    """Appointments in this LGU as PDS work experience rows, newest first (PD3). Salary, grade and step
    are pay data (R6): blank for roles that cannot see pay, for the employee to write in."""
    rows = []
    for eng in person.engagements.select_related("office").order_by("-start_date", "-pk"):
        salary = ""
        if show_pay and eng.rate is not None:
            salary = (f"{eng.rate:,.2f}" if eng.rate_basis == Engagement.RateBasis.MONTHLY
                      else f"{eng.rate:,.2f} ({eng.get_rate_basis_display().lower()})")
        rows.append({
            "date_from": eng.start_date,
            "date_to": eng.end_date if eng.status != Engagement.Status.ACTIVE else None,
            "position": eng.position_title,
            "employer": f"LGU San Pascual, {eng.office.name}",
            "salary": salary,
            "grade_step": f"{eng.salary_grade}-{eng.step}" if show_pay and eng.salary_grade else "",
            "appointment_status": status_text(eng),
            "government": eng.type not in excluded_types(),
            "lgu": True,
        })
    return rows


def work_rows(person, loaded, show_pay):
    """LGU appointments and the work the person entered, newest first, as the PDS lists them."""
    entered = [{**r, "salary": f"{Decimal(r['monthly_salary']):,.2f}" if r.get("monthly_salary") else "", "lgu": False}
               for r in loaded["work"]]
    return sorted(lgu_work_rows(person, show_pay) + entered, key=lambda r: r["date_from"], reverse=True)


def question_rows(profile, form=None):
    """Questions 34-40 with each answer and its details, from a loaded profile or bound to a form."""
    rows = []
    for number, intro, items in QUESTIONS:
        lines = []
        for answer, text, details in items:
            details = details or None
            line = {"text": text, "answer": answer}
            if form is not None:
                line["field"] = form[answer]
                line["details"] = form[details] if details else None
                if answer == "q35b":
                    line["extra"] = [form["q35b_date_filed"], form["q35b_status"]]
            else:
                line["value"] = profile.get(answer)
                line["details"] = profile.get(details) if details else None
                if answer == "q35b":
                    line["extra"] = [("Date filed", profile.get("q35b_date_filed")),
                                     ("Status of case/s", profile.get("q35b_status"))]
            line["details_label"] = DETAIL_LABELS.get(details, "If YES, give details")
            lines.append(line)
        rows.append({"number": number, "intro": intro, "lines": lines})
    return rows
