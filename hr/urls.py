from django.urls import path
from . import views

urlpatterns = [
    path("offices/", views.office_list, name="office_list"),
    path("offices/<int:pk>/", views.office_detail, name="office_detail"),
    path("people/", views.person_list, name="person_list"),
    path("people/new/", views.person_create, name="person_create"),
    path("people/<int:pk>/", views.person_detail, name="person_detail"),
    path("people/<int:pk>/edit/", views.person_edit, name="person_edit"),
    path("documents/<int:pk>/download/", views.document_download, name="document_download"),
    path("audit/", views.audit_list, name="audit_list"),
    path("audit/export/csv/", views.audit_export_csv, name="audit_export_csv"),
]



