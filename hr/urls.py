from django.urls import path
from . import views

urlpatterns = [
    path("offices/", views.office_list, name="office_list"),
    path("offices/<int:pk>/", views.office_detail, name="office_detail"),
    path("offices/chart/", views.office_tree, name="office_tree"),
    path("offices/chart/export/<str:fmt>/", views.office_tree_export, name="office_tree_export"),
    path("offices/<int:pk>/chart/", views.office_chart, name="office_chart"),
    path("offices/<int:pk>/chart/edit/", views.office_chart_edit, name="office_chart_edit"),
    path("offices/<int:pk>/chart/export/<str:fmt>/", views.office_chart_export, name="office_chart_export"),
    path("my-office/", views.my_office, name="my_office"),
    path("salary-actions/", views.salary_action_list, name="salary_action_list"),
    path("salary-actions/check/", views.salary_action_check_now, name="salary_action_check_now"),
    path("salary-actions/<int:pk>/", views.salary_action_detail, name="salary_action_detail"),
    path("salary-actions/<int:pk>/interruptions/", views.salary_interruption_create, name="salary_interruption_create"),
    path("people/", views.person_list, name="person_list"),
    path("people/new/", views.person_create, name="person_create"),
    path("people/<int:pk>/", views.person_detail, name="person_detail"),
    path("people/<int:pk>/edit/", views.person_edit, name="person_edit"),
    path("people/<int:person_pk>/engagements/new/", views.engagement_create, name="engagement_create"),
    path("people/<int:person_pk>/documents/new/", views.document_create_person, name="document_create_person"),
    path("engagements/<int:engagement_pk>/documents/new/", views.document_create_engagement, name="document_create_engagement"),
    path("engagements/<int:pk>/edit/", views.engagement_edit, name="engagement_edit"),
    path("engagements/<int:pk>/end/", views.engagement_end, name="engagement_end"),
    path("engagements/<int:pk>/renew/", views.engagement_renew, name="engagement_renew"),
    path("documents/<int:pk>/edit/", views.document_edit, name="document_edit"),
    path("documents/<int:pk>/replace/", views.document_replace, name="document_replace"),
    path("documents/<int:pk>/download/", views.document_download, name="document_download"),
    path("projects/", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/<int:pk>/", views.project_detail, name="project_detail"),
    path("projects/<int:pk>/edit/", views.project_edit, name="project_edit"),
    path("calendar/", views.calendar_month, name="calendar"),
    path("calendar/day/<str:day>/", views.calendar_day, name="calendar_day"),
    path("calendar/events/new/", views.calendar_event_form, name="calendar_event_create"),
    path("calendar/events/<int:pk>/", views.calendar_event_form, name="calendar_event_edit"),
    path("audit/", views.audit_list, name="audit_list"),
    path("audit/reverify/", views.audit_reverify, name="audit_reverify"),
    path("audit/export/csv/", views.audit_export_csv, name="audit_export_csv"),
]



