from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from .models import (
    Applicant,
    AuditLog,
    Delegate,
    Document,
    Engagement,
    LeaveApprovalStep,
    LeaveCredit,
    LeaveRequest,
    Memo,
    MemoAcknowledgment,
    Office,
    OrgUnit,
    StructurePosition,
    Person,
    Project,
    UserProfile,
    Vacancy,
)

from .accounts import require_password_change
from .permissions import PROTECTED_ROLES, hr_access_accounts


class CustomUserAdmin(BaseUserAdmin):
    """Custom User admin preventing privilege escalation by System Admin (DECISIONS R2/R5).

    - Non-superusers cannot view, edit, delete or reset the password of any account that reaches
      HR records (hr_access_accounts): superusers, protected roles, employee self-service accounts,
      delegates and accounts with direct HR permissions. Otherwise a password reset would let
      System Admin log in as that person.
    - Non-superusers cannot assign protected roles (groups) or direct HR permissions.
    - Non-superusers cannot escalate privileges via is_superuser.
    - A password set by an admin for someone else is one-time: the user must change it at
      next login, so an admin cannot quietly keep using an account they reset.
    """

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            qs = qs.exclude(pk__in=hr_access_accounts(User.objects.all()).values("pk"))
        return qs

    @staticmethod
    def _reaches_hr(obj):
        return hr_access_accounts(User.objects.filter(pk=obj.pk)).exists()

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj and not request.user.is_superuser and self._reaches_hr(obj):
            return False
        return True

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj and not request.user.is_superuser and self._reaches_hr(obj):
            return False
        return True

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            if db_field.name == "groups":
                kwargs["queryset"] = Group.objects.exclude(name__in=PROTECTED_ROLES)
            elif db_field.name == "user_permissions":
                # Non-superusers cannot grant direct HR app permissions
                kwargs["queryset"] = db_field.remote_field.model.objects.exclude(content_type__app_label="hr")
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            # Force is_superuser to False
            obj.is_superuser = False
        super().save_model(request, obj, form, change)
        if not change and obj.has_usable_password():
            require_password_change(obj)

    def user_change_password(self, request, id, form_url=""):
        response = super().user_change_password(request, id, form_url)
        # A redirect means the new password was saved.
        if request.method == "POST" and response.status_code == 302 and str(request.user.pk) != str(id):
            require_password_change(User.objects.get(pk=id))
        return response

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not request.user.is_superuser:
            # Guard against forged POST requests attempting to grant protected roles
            protected_assigned = form.instance.groups.filter(name__in=PROTECTED_ROLES)
            if protected_assigned.exists():
                form.instance.groups.remove(*protected_assigned)
                raise PermissionDenied("Only emergency superusers can assign protected roles.")


class CustomGroupAdmin(BaseGroupAdmin):
    """Custom Group admin ensuring only superusers can modify groups or group permissions."""

    def has_add_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass

admin.site.register(User, CustomUserAdmin)
admin.site.register(Group, CustomGroupAdmin)


class SuperuserOnlyAdmin(admin.ModelAdmin):
    """Restricts HR models in Django /admin/ to emergency superusers only (DECISIONS R2).

    System Admin (IT) and other staff users can only manage auth users and groups in /admin/.
    All HR data must be managed through standard application screens with full audit logging.
    """

    def has_module_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_change_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_add_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)


@admin.register(Office)
class OfficeAdmin(SuperuserOnlyAdmin):
    list_display = ("name", "acronym", "branch", "type", "parent", "coterminous", "head_title")
    list_filter = ("branch", "type", "coterminous")
    search_fields = ("name", "acronym", "head_title")


@admin.register(OrgUnit)
class OrgUnitAdmin(SuperuserOnlyAdmin):
    list_display = ("name", "office", "kind", "parent", "beside_head", "order")
    list_filter = ("office", "kind")


@admin.register(StructurePosition)
class StructurePositionAdmin(SuperuserOnlyAdmin):
    list_display = ("title", "salary_grade", "status", "office", "unit", "beside_head", "coterminous", "order")
    list_filter = ("office", "status")
    search_fields = ("title",)


class EngagementInline(admin.TabularInline):
    model = Engagement
    extra = 0
    fields = ("office", "type", "position_title", "status", "start_date", "end_date", "leave_eligible")


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    fields = ("doc_type", "status", "original_on_file", "physical_location", "uploaded_at")
    readonly_fields = ("uploaded_at",)


@admin.register(Person)
class PersonAdmin(SuperuserOnlyAdmin):
    list_display = ("employee_no", "last_name", "first_name", "sex", "birthdate", "mobile")
    search_fields = ("employee_no", "last_name", "first_name")
    inlines = [EngagementInline, DocumentInline]


@admin.register(Engagement)
class EngagementAdmin(SuperuserOnlyAdmin):
    list_display = ("person", "office", "type", "position_title", "status", "leave_eligible", "start_date", "end_date")
    list_filter = ("type", "status", "leave_eligible", "office")
    search_fields = ("person__last_name", "person__first_name", "position_title")


@admin.register(Project)
class ProjectAdmin(SuperuserOnlyAdmin):
    list_display = ("name", "office", "status", "start_date", "end_date")
    list_filter = ("status", "office")
    search_fields = ("name",)


@admin.register(Document)
class DocumentAdmin(SuperuserOnlyAdmin):
    list_display = ("doc_type", "person", "engagement", "status", "original_on_file", "physical_location", "uploaded_at")
    list_filter = ("doc_type", "status", "original_on_file")
    search_fields = ("person__last_name", "physical_location", "notes")


@admin.register(LeaveRequest)
class LeaveRequestAdmin(SuperuserOnlyAdmin):
    list_display = ("person", "leave_type", "start_date", "end_date", "days", "status", "approver")
    list_filter = ("leave_type", "status")
    search_fields = ("person__last_name", "person__first_name")


@admin.register(LeaveApprovalStep)
class LeaveApprovalStepAdmin(SuperuserOnlyAdmin):
    list_display = ("request", "order", "kind", "status", "acted_by", "acted_at", "from_paper")
    list_filter = ("kind", "status")


@admin.register(Vacancy)
class VacancyAdmin(SuperuserOnlyAdmin):
    list_display = ("position_title", "item_number", "office", "status", "deadline")
    list_filter = ("status", "office")


@admin.register(Applicant)
class ApplicantAdmin(SuperuserOnlyAdmin):
    list_display = ("last_name", "first_name", "vacancy", "signified_on", "meets_qs", "rank")


@admin.register(Memo)
class MemoAdmin(SuperuserOnlyAdmin):
    list_display = ("number", "subject", "dated", "audience", "is_published")


@admin.register(MemoAcknowledgment)
class MemoAcknowledgmentAdmin(SuperuserOnlyAdmin):
    list_display = ("memo", "person", "acknowledged_at", "from_paper")


@admin.register(LeaveCredit)
class LeaveCreditAdmin(SuperuserOnlyAdmin):
    list_display = ("person", "leave_type", "days", "as_of", "note")
    list_filter = ("leave_type",)
    search_fields = ("person__last_name", "person__first_name")


@admin.register(UserProfile)
class UserProfileAdmin(SuperuserOnlyAdmin):
    list_display = ("user", "person", "office", "must_change_password")
    search_fields = ("user__username", "person__last_name")


@admin.register(Delegate)
class DelegateAdmin(SuperuserOnlyAdmin):
    list_display = ("principal", "delegate", "valid_from", "valid_to", "notes")
    search_fields = ("principal__username", "delegate__username")


@admin.register(AuditLog)
class AuditLogAdmin(SuperuserOnlyAdmin):
    list_display = ("timestamp", "username", "action", "model_name", "object_id", "object_repr", "ip_address")
    list_filter = ("action", "model_name")
    search_fields = ("username", "object_repr", "object_id")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

