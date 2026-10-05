from django.contrib import admin
from .models import (
    Document,
    Engagement,
    LeaveCredit,
    LeaveRequest,
    Office,
    Person,
    Project,
    UserProfile,
)


@admin.register(Office)
class OfficeAdmin(admin.ModelAdmin):
    list_display = ("name", "acronym", "branch", "type", "parent", "head_title")
    list_filter = ("branch", "type")
    search_fields = ("name", "acronym", "head_title")


class EngagementInline(admin.TabularInline):
    model = Engagement
    extra = 0
    fields = ("office", "type", "position_title", "status", "start_date", "end_date")


class DocumentInline(admin.TabularInline):
    model = Document
    extra = 0
    fields = ("doc_type", "status", "physical_location", "uploaded_at")
    readonly_fields = ("uploaded_at",)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("employee_no", "last_name", "first_name", "sex", "birthdate", "mobile")
    search_fields = ("employee_no", "last_name", "first_name")
    inlines = [EngagementInline, DocumentInline]


@admin.register(Engagement)
class EngagementAdmin(admin.ModelAdmin):
    list_display = ("person", "office", "type", "position_title", "status", "start_date", "end_date")
    list_filter = ("type", "status", "office")
    search_fields = ("person__last_name", "person__first_name", "position_title")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "office", "status", "start_date", "end_date")
    list_filter = ("status", "office")
    search_fields = ("name",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("doc_type", "person", "engagement", "status", "physical_location", "uploaded_at")
    list_filter = ("doc_type", "status")
    search_fields = ("person__last_name", "physical_location", "notes")


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ("person", "leave_type", "start_date", "end_date", "days", "status", "approver")
    list_filter = ("leave_type", "status")
    search_fields = ("person__last_name", "person__first_name")


@admin.register(LeaveCredit)
class LeaveCreditAdmin(admin.ModelAdmin):
    list_display = ("person", "leave_type", "days", "as_of", "note")
    list_filter = ("leave_type",)
    search_fields = ("person__last_name", "person__first_name")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "person", "office", "must_change_password")
    search_fields = ("user__username", "person__last_name")
