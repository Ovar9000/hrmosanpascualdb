from django.contrib import admin
from .models import (
    Delegate,
    Document,
    Engagement,
    LeaveCredit,
    LeaveRequest,
    Office,
    Person,
    Project,
    UserProfile,
)


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
    list_display = ("name", "acronym", "branch", "type", "parent", "head_title")
    list_filter = ("branch", "type")
    search_fields = ("name", "acronym", "head_title")


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
