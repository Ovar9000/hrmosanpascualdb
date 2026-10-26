"""Audit log signals for model mutations, authentication events, and security locks."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver
from .audit import (
    SENSITIVE_AUDIT_FIELDS,
    is_audit_disabled,
    log_audit_event,
    serialize_field_value,
)
from .mfa import SESSION_VERIFIED
from .models import (
    Applicant,
    AuditLog,
    CalendarEvent,
    CertificateRequest,
    CocEntry,
    Delegate,
    PerformanceRating,
    SalnFiling,
    PdsChild,
    PdsEducation,
    PdsEligibility,
    PdsProfile,
    PasswordResetRequest,
    PdsReference,
    PdsSubmission,
    PdsTraining,
    PdsVoluntaryWork,
    PdsWorkExperience,
    Document,
    Engagement,
    LeaveApprovalStep,
    LeaveCredit,
    LeaveRequest,
    Meeting,
    MeetingSeries,
    Memo,
    MemoAcknowledgment,
    Office,
    OrgUnit,
    Person,
    Project,
    SalaryAction,
    ServiceInterruption,
    StructurePosition,
    UserProfile,
    Vacancy,
)

User = get_user_model()

AUDITED_MODELS = (
    Person,
    Engagement,
    Document,
    LeaveRequest,
    LeaveCredit,
    Delegate,
    UserProfile,
    Office,
    OrgUnit,
    StructurePosition,
    Project,
    CalendarEvent,
    SalaryAction,
    ServiceInterruption,
    MeetingSeries,
    Meeting,
    LeaveApprovalStep,
    Vacancy,
    Applicant,
    Memo,
    MemoAcknowledgment,
    CertificateRequest,
    PdsProfile,
    PdsChild,
    PdsEducation,
    PdsEligibility,
    PdsWorkExperience,
    PdsVoluntaryWork,
    PdsTraining,
    PasswordResetRequest,
    PdsReference,
    PdsSubmission,
    PerformanceRating,
    SalnFiling,
    CocEntry,
    User,
    Group,
)


@receiver(pre_save)
def capture_pre_save_state(sender, instance, **kwargs):
    if is_audit_disabled() or sender not in AUDITED_MODELS or sender is AuditLog:
        return

    if instance.pk:
        try:
            old_inst = sender.objects.filter(pk=instance.pk).first()
            if old_inst:
                instance._pre_save_state = {
                    f.name: getattr(old_inst, f.name, None)
                    for f in old_inst._meta.fields
                }
        except Exception:
            instance._pre_save_state = None
    else:
        instance._pre_save_state = None


@receiver(post_save)
def log_model_mutation(sender, instance, created, **kwargs):
    if is_audit_disabled() or sender not in AUDITED_MODELS or sender is AuditLog:
        return

    model_name = sender.__name__
    object_id = str(instance.pk)
    object_repr = str(instance)

    if created:
        changes = {}
        for f in instance._meta.fields:
            if f.name in ["id", "pk"]:
                continue
            val = getattr(instance, f.name, None)
            if val is not None and val != "":
                if f.name in SENSITIVE_AUDIT_FIELDS:
                    changes[f.name] = "[REDACTED]"
                else:
                    changes[f.name] = {"old": None, "new": serialize_field_value(val)}

        log_audit_event(
            action=AuditLog.Action.CREATE,
            model_name=model_name,
            object_id=object_id,
            object_repr=object_repr,
            changes=changes,
        )
    else:
        old_state = getattr(instance, "_pre_save_state", None) or {}
        changes = {}
        password_changed = False

        for f in instance._meta.fields:
            if f.name in ["id", "pk"]:
                continue
            old_val = old_state.get(f.name)
            new_val = getattr(instance, f.name, None)
            if old_val != new_val:
                if f.name in SENSITIVE_AUDIT_FIELDS:
                    changes[f.name] = "[REDACTED]"
                    if f.name == "password":
                        password_changed = True
                else:
                    changes[f.name] = {
                        "old": serialize_field_value(old_val),
                        "new": serialize_field_value(new_val),
                    }

        if changes:
            # Do not log User UPDATE events when only last_login changed (Requirement 2)
            if sender is User and list(changes.keys()) == ["last_login"]:
                return

            action = (
                AuditLog.Action.PASSWORD_RESET
                if (sender is User and password_changed)
                else AuditLog.Action.UPDATE
            )
            log_audit_event(
                action=action,
                model_name=model_name,
                object_id=object_id,
                object_repr=object_repr,
                changes=changes,
            )


@receiver(post_delete)
def log_model_deletion(sender, instance, **kwargs):
    if is_audit_disabled() or sender not in AUDITED_MODELS or sender is AuditLog:
        return

    log_audit_event(
        action=AuditLog.Action.DELETE,
        model_name=sender.__name__,
        object_id=str(instance.pk),
        object_repr=str(instance),
    )


@receiver(m2m_changed)
def log_user_group_changes(sender, instance, action, pk_set, **kwargs):
    if is_audit_disabled():
        return

    # Check if this m2m is User.groups
    if sender == User.groups.through and action in ["post_add", "post_remove", "post_clear"]:
        user_inst = instance
        group_names = list(Group.objects.filter(pk__in=pk_set).values_list("name", flat=True)) if pk_set else []
        log_audit_event(
            action=AuditLog.Action.GROUP_CHANGE,
            model_name="User",
            object_id=str(user_inst.pk),
            object_repr=f"User {user_inst.get_username()} groups modified ({action})",
            changes={"m2m_action": action, "groups": group_names},
            user=user_inst,
        )


# --- Authentication Signals ---------------------------------------------------


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    # Every login starts before the second step, even one into a session that had passed it (A5).
    if request is not None and hasattr(request, "session"):
        request.session.pop(SESSION_VERIFIED, None)
    if is_audit_disabled():
        return
    log_audit_event(
        action=AuditLog.Action.LOGIN,
        model_name="User",
        object_id=str(user.pk),
        object_repr=f"User {user.get_username()} logged in",
        user=user,
        request=request,
    )


@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    if is_audit_disabled():
        return
    if user:
        log_audit_event(
            action=AuditLog.Action.LOGOUT,
            model_name="User",
            object_id=str(user.pk),
            object_repr=f"User {user.get_username()} logged out",
            user=user,
            request=request,
        )


def mask_attempted_username(raw_username):
    """Keeps an attempted username only if it is a real account; otherwise "[unknown]".

    The audit log can never be purged, so a password typed into the username field
    must not be written to it (DECISIONS S9).
    """
    raw_username = str(raw_username or "")[:150]
    if raw_username and "\x00" not in raw_username and User.objects.filter(username=raw_username).exists():
        return raw_username
    return "[unknown]"


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request, **kwargs):
    if is_audit_disabled():
        return
    safe_username = mask_attempted_username(credentials.get("username", "") if credentials else "")

    log_audit_event(
        action=AuditLog.Action.FAILED_LOGIN,
        model_name="User",
        username=safe_username,
        object_repr=f"Failed login attempt for '{safe_username}'",
        changes={"username_attempted": safe_username},
        request=request,
    )


# Axes lockout signal (try import axes signal)
try:
    from axes.signals import user_locked_out

    @receiver(user_locked_out)
    def log_axes_lockout(sender, request, username, ip_address, **kwargs):
        if is_audit_disabled():
            return
        safe_username = mask_attempted_username(username)
        log_audit_event(
            action=AuditLog.Action.ACCOUNT_LOCKOUT,
            model_name="User",
            username=safe_username,
            ip_address=ip_address,
            object_repr=f"Account lockout triggered for '{safe_username}' from {ip_address}",
            changes={"username": safe_username, "ip_address": ip_address},
            request=request,
        )
except ImportError:
    pass
