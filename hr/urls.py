from django.urls import path
from . import views

urlpatterns = [
    path("offices/", views.office_list, name="office_list"),
    path("offices/<int:pk>/", views.office_detail, name="office_detail"),
    path("people/", views.person_list, name="person_list"),
    path("people/<int:pk>/", views.person_detail, name="person_detail"),
    path("documents/<int:pk>/download/", views.document_download, name="document_download"),
]

