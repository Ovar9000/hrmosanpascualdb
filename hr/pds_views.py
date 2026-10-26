"""Screens for the Personal Data Sheet: the person's own sections, HRMO's verification queue, and the
printed CS Form No. 212 (DECISIONS PD1-PD7)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.forms import modelformset_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import pds
from .audit import log_audit_event
from .forms import (
    PdsChildForm, PdsEducationForm, PdsEligibilityForm, PdsFamilyForm, PdsGovIdForm, PdsOtherForm, PdsPersonalForm,
    PdsQuestionsForm, PdsReferenceForm, PdsTrainingForm, PdsVoluntaryForm, PdsWorkForm,
)
from .models import AuditLog, PdsProfile, PdsSubmission, Person
from .permissions import (
    can_edit_pds, can_verify_pds, can_view_pay, can_view_pds, can_view_pds_queue, is_self,
)

Sub = PdsSubmission

# Section key -> (profile form, (row form, rows per new table, most rows), intro text)
EDITORS = {
    "personal": (PdsPersonalForm, None,
                 "Your name, birthdate, sex, mobile, email and government ID numbers are kept by HRMO and shown here "
                 "as they are on file. Ask HRMO to correct them."),
    "family": (PdsFamilyForm, (PdsChildForm, 1, 30),
               "Spouse (if married), parents, and all children. Your mother's name is her maiden name."),
    "education": (None, (PdsEducationForm, 1, 20),
                  "One row per level: elementary, secondary, vocational, college and graduate studies."),
    "eligibility": (None, (PdsEligibilityForm, 1, 20),
                    "Civil service eligibility, board or bar passed, and licenses. Leave empty if you have none."),
    "work": (None, (PdsWorkForm, 1, 40),
             "Work before or outside the Municipality. Your appointments here are filled in for you from HRMO's "
             "records, so do not add them again."),
    "voluntary": (None, (PdsVoluntaryForm, 1, 20),
                  "Involvement in civic, non-government, people's or voluntary organizations."),
    "training": (None, (PdsTrainingForm, 1, 60),
                 "Trainings, seminars and other learning and development, newest first. Include the hours."),
    "other": (PdsOtherForm, None, "One item per line."),
    "questions": (PdsQuestionsForm, None, "Answer every question. If you answer Yes, give the details."),
    "references": (PdsGovIdForm, (PdsReferenceForm, 3, 3),
                   "Three people not related to you, and the government-issued ID you will show when you swear to the "
                   "PDS."),
}


ADDRESS = ["house", "street", "subdivision", "barangay", "city", "province", "zip"]
# How a section's profile fields are grouped on its page.
GROUPS = {
    "personal": [
        ("Personal details", ["place_of_birth", "civil_status", "civil_status_other", "height_m", "weight_kg",
                              "blood_type", "citizenship", "dual_by", "dual_country", "telephone"]),
        ("Residential address", [f"res_{a}" for a in ADDRESS]),
        ("Permanent address", ["perm_same"] + [f"perm_{a}" for a in ADDRESS]),
    ],
    "family": [
        ("Spouse", ["spouse_surname", "spouse_first_name", "spouse_middle_name", "spouse_extension",
                    "spouse_occupation", "spouse_employer", "spouse_business_address", "spouse_telephone"]),
        ("Father", ["father_surname", "father_first_name", "father_middle_name", "father_extension"]),
        ("Mother's maiden name", ["mother_surname", "mother_first_name", "mother_middle_name"]),
    ],
    "other": [("Skills, distinctions and memberships", ["skills", "distinctions", "memberships"])],
    "references": [("Government-issued ID", ["gov_id_type", "gov_id_number", "gov_id_issued"])],
}
# Title and hint of each section's table of rows.
ROWS = {
    "family": ("Children", "List all, with full names"),
    "education": ("Schools attended", "One row per level"),
    "eligibility": ("Eligibility and licenses", ""),
    "work": ("Before or outside the Municipality", "Newest first"),
    "voluntary": ("Organizations", ""),
    "training": ("Trainings attended", "Newest first"),
    "references": ("References", "Three people not related to you"),
}


def _own_person(user):
    profile = getattr(user, "profile", None)
    return profile.person if profile and profile.person_id else None


def _person(request, pk, edit=False):
    person = get_object_or_404(Person, pk=pk)
    if not can_view_pds(request.user, person):
        raise PermissionDenied("The Personal Data Sheet is seen only by the person, HRMO and the Auditor.")
    if edit and not can_edit_pds(request.user, person):
        raise PermissionDenied("Only the person or HRMO can change the Personal Data Sheet.")
    return person


def _can_verify_pds_of(user, person):
    """HRMO verifies, but never their own PDS: another HRMO staff member does (R10)."""
    return can_verify_pds(user) and not is_self(user, person)


@login_required
def pds_home(request):
    person = _own_person(request.user)
    if person is not None:
        return redirect("pds_overview", pk=person.pk)
    if can_view_pds_queue(request.user):
        return redirect("pds_queue")
    raise PermissionDenied("Your account is not linked to a personnel record. Ask HRMO.")


@login_required
def pds_overview(request, pk):
    """The whole PDS as a checklist of sections, then every section as it reads now (PD2)."""
    person = _person(request, pk)
    st = pds.state(person)
    loaded = pds.display(st["snap"])
    show_pay = can_view_pay(request.user)
    rows = pds.completeness(st["snap"])
    for row in rows:
        if row["key"] == "work":  # appointments here count too (PD3)
            row["count"] += person.engagements.count()
    required = [r for r in rows if r["required"]]
    return render(request, "hr/pds_overview.html", {
        "person": person,
        "st": st,
        "d": loaded,
        "work": pds.work_rows(person, loaded, show_pay),
        "sections": rows,
        "progress": {"done": sum(1 for r in required if r["done"]), "total": len(required),
                     "pct": round(100 * sum(1 for r in required if r["done"]) / len(required))},
        "qrows": pds.question_rows(loaded["profile"]),
        "can_edit": can_edit_pds(request.user, person),
        "can_verify": _can_verify_pds_of(request.user, person),
        "is_self": is_self(request.user, person),
        "history": Sub.objects.filter(person=person).select_related("reviewed_by")[:6],
        "changed": st["unsent"],
    })


def _formset(person, section, data=None, hide_hrmo=False):
    editor = EDITORS[section][1]
    if editor is None:
        return None
    form_class, extra, most = editor
    model = form_class._meta.model
    queryset = model.objects.filter(person=person)
    if hide_hrmo:  # trainings HRMO recorded are shown read-only to the employee (LD2)
        queryset = queryset.filter(by_hrmo=False)
    count = queryset.count()
    # References always show three rows; other tables one blank row (more with "Add a row").
    blank = max(extra - count, 0) if section == "references" else extra
    factory = modelformset_factory(model, form=form_class, can_delete=True, max_num=most, validate_max=True,
                                   extra=blank)
    return factory(data, queryset=queryset, prefix=section)


@login_required
def pds_section(request, pk, section):
    """Edit one section: its fields on PdsProfile, its table of rows, or both."""
    if section not in EDITORS:
        raise Http404("No such section.")
    person = _person(request, pk, edit=True)
    form_class, _rows, intro = EDITORS[section]
    # The profile row is made on the first save, so opening a page changes nothing.
    profile = pds.profile_for(person, create=request.method == "POST") if form_class else None
    form = formset = None
    hide_hrmo = section == "training" and not _can_verify_pds_of(request.user, person)
    if request.method == "POST":
        form = form_class(request.POST, instance=profile) if form_class else None
        formset = _formset(person, section, request.POST, hide_hrmo)
        if (form is None or form.is_valid()) and (formset is None or formset.is_valid()):
            stale = False
            with transaction.atomic():
                if form is not None:
                    locked = PdsProfile.objects.select_for_update().get(pk=profile.pk)
                    if locked.version != form.cleaned_data.get("version"):
                        stale = True
                    else:
                        saved = form.save(commit=False)
                        saved.version = locked.version + 1
                        saved.save()
                if formset is not None and not stale:
                    for row in formset.save(commit=False):
                        row.person = person
                        row.save()
                    for row in formset.deleted_objects:
                        row.delete()
            if stale:
                form.add_error(None, "Someone changed this part of the PDS while you were editing. Reload and try again.")
            else:
                messages.success(request, f"Saved {pds.TITLES[section]}.")
                index = pds.SECTION_KEYS.index(section)
                if "next" in request.POST and index + 1 < len(pds.SECTION_KEYS):
                    return redirect("pds_section", pk=pk, section=pds.SECTION_KEYS[index + 1])
                return redirect("pds_overview", pk=pk)
    else:
        form = form_class(instance=profile) if form_class else None
        formset = _formset(person, section, hide_hrmo=hide_hrmo)
    index = pds.SECTION_KEYS.index(section)
    return render(request, "hr/pds_section.html", {
        "person": person, "section": section, "title": pds.TITLES[section], "intro": intro,
        "is_self": is_self(request.user, person),
        "number": index + 1,
        "formset_first": section == "references",
        "rows_title": ROWS.get(section, ("", ""))[0],
        "rows_hint": ROWS.get(section, ("", ""))[1],
        "form": form, "formset": formset,
        "qrows": pds.question_rows(None, form) if section == "questions" else None,
        "groups": [(heading, [form[name] for name in names]) for heading, names in GROUPS.get(section, [])]
                  if form is not None else [],
        "next_title": pds.TITLES[pds.SECTION_KEYS[index + 1]] if index + 1 < len(pds.SECTION_KEYS) else None,
        "lgu_work": pds.lgu_work_rows(person, can_view_pay(request.user)) if section == "work" else None,
        "hrmo_trainings": person.pds_training.filter(by_hrmo=True) if hide_hrmo else None,
        "steps": [(key, pds.TITLES[key]) for key in pds.SECTION_KEYS],
    })


@login_required
def pds_submit(request, pk):
    """Freeze the PDS and send it to HRMO. A newer send replaces one HRMO has not looked at yet. HRMO
    encoding a paper PDS can verify it in the same step (PD4)."""
    if request.method != "POST":
        return redirect("pds_overview", pk=pk)
    person = _person(request, pk, edit=True)
    snap = pds.snapshot(person)
    if pds.missing(snap):
        messages.error(request, "Fill in " + ", ".join(r["title"] for r in pds.missing(snap)) + " first.")
        return redirect("pds_overview", pk=pk)
    verify_now = request.POST.get("verify") == "1" and _can_verify_pds_of(request.user, person)
    try:
        with transaction.atomic():
            waiting = Sub.objects.select_for_update().filter(person=person, status=Sub.Status.SUBMITTED).first()
            verified = Sub.objects.filter(person=person, status=Sub.Status.VERIFIED).first()
            if waiting is None and verified and not pds.changed_sections(verified.snapshot, snap) and not verify_now:
                messages.info(request, "Nothing has changed since HRMO verified it.")
                return redirect("pds_overview", pk=pk)
            now = timezone.now()
            sub = waiting or Sub(person=person)
            sub.snapshot, sub.submitted_at, sub.submitted_by = snap, now, request.user
            if verify_now:
                sub.status, sub.reviewed_at, sub.reviewed_by = Sub.Status.VERIFIED, now, request.user
                sub.note = "Encoded by HRMO and verified."
            if waiting:
                sub.version += 1
            sub.save()
    except IntegrityError:
        messages.error(request, "It was just sent from another window. Check it again.")
        return redirect("pds_overview", pk=pk)
    if verify_now:
        messages.success(request, "Saved and verified.")
    elif waiting:
        messages.success(request, "Your changes replace the copy waiting for HRMO.")
    else:
        messages.success(request, "Sent to HRMO for verification. You can keep it up to date here any time.")
    return redirect("pds_overview", pk=pk)


@login_required
def pds_queue(request):
    """PDS waiting for HRMO, oldest first, and the latest decisions (PD4)."""
    if not can_view_pds_queue(request.user):
        raise PermissionDenied("The verification queue is HRMO's.")
    show = "done" if request.GET.get("show") == "done" else "waiting"
    rows = Sub.objects.select_related("person", "submitted_by", "reviewed_by")
    rows = (rows.filter(status=Sub.Status.SUBMITTED).order_by("submitted_at") if show == "waiting"
            else rows.exclude(status=Sub.Status.SUBMITTED).order_by("-reviewed_at"))
    page = Paginator(rows, 25).get_page(request.GET.get("page"))
    for sub in page:
        if show == "waiting":
            previous = Sub.objects.filter(person=sub.person, status=Sub.Status.VERIFIED).first()
            sub.changed = [pds.TITLES[k] for k in pds.SECTION_KEYS
                           if k in pds.changed_sections(previous.snapshot if previous else None, sub.snapshot)]
            sub.first_time = previous is None
    return render(request, "hr/pds_queue.html", {
        "page": page, "show": show,
        "waiting": Sub.objects.filter(status=Sub.Status.SUBMITTED).count(),
    })


@login_required
def pds_review(request, sub_pk):
    """A submitted copy as HRMO received it, with what changed since the last verified one; HRMO
    verifies it or returns it with what to correct."""
    sub = get_object_or_404(Sub.objects.select_related("person", "submitted_by", "reviewed_by"), pk=sub_pk)
    person = _person(request, sub.person_id)
    if request.method == "POST":
        if not _can_verify_pds_of(request.user, person):
            raise PermissionDenied("Only HRMO verifies a PDS, and another HRMO staff member verifies your own (R10).")
        action, note = request.POST.get("action"), request.POST.get("note", "").strip()
        with transaction.atomic():
            locked = Sub.objects.select_for_update().get(pk=sub.pk)
            if locked.status != Sub.Status.SUBMITTED or str(locked.version) != request.POST.get("version", ""):
                messages.error(request, "This copy changed or was decided while you had it open. Check it again.")
                return redirect("pds_review", sub_pk=sub.pk)
            if action == "return" and not note:
                messages.error(request, "Say what to correct, so the person knows what to fix.")
                return redirect("pds_review", sub_pk=sub.pk)
            if action not in ("verify", "return"):
                raise PermissionDenied("Unknown action.")
            locked.status = Sub.Status.VERIFIED if action == "verify" else Sub.Status.RETURNED
            locked.note, locked.reviewed_at, locked.reviewed_by = note, timezone.now(), request.user
            locked.version += 1
            locked.save()
        messages.success(request, "Verified." if action == "verify" else "Returned for correction.")
        nxt = (Sub.objects.filter(status=Sub.Status.SUBMITTED).exclude(person=_own_person(request.user))
               .order_by("submitted_at").first())
        return redirect("pds_review", sub_pk=nxt.pk) if nxt else redirect("pds_queue")
    previous = (Sub.objects.filter(person=person, status=Sub.Status.VERIFIED, submitted_at__lt=sub.submitted_at)
                .exclude(pk=sub.pk).first())
    loaded = pds.display(sub.snapshot)
    show_pay = can_view_pay(request.user)
    return render(request, "hr/pds_review.html", {
        "sub": sub, "person": person, "d": loaded,
        "work": pds.work_rows(person, loaded, show_pay),
        "changed": pds.changed_sections(previous.snapshot if previous else None, sub.snapshot),
        "previous": previous,
        "qrows": pds.question_rows(loaded["profile"]),
        "can_verify": _can_verify_pds_of(request.user, person) and sub.status == Sub.Status.SUBMITTED,
    })


@login_required
def pds_print(request, pk):
    """CS Form No. 212 filled in, for signature, thumbmark and oath (the signed paper is the record, F1)."""
    person = _person(request, pk)
    snap = pds.snapshot(person)
    loaded = pds.display(snap)
    show_pay = can_view_pay(request.user)
    verified = Sub.objects.filter(person=person, status=Sub.Status.VERIFIED).first()
    log_audit_event(action=AuditLog.Action.EXPORT, model_name="Person", object_id=person.pk,
                    object_repr=f"PDS print: {person}"[:255], request=request)
    return render(request, "hr/pds_print.html", {
        "person": person, "d": loaded, "work": pds.work_rows(person, loaded, show_pay),
        "qrows": pds.question_rows(loaded["profile"]), "today": timezone.localdate(),
        "verified": verified,
        "verified_current": verified is not None and not pds.changed_sections(verified.snapshot, snap),
        "autoprint": request.GET.get("autoprint") == "1",
    })
