from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from hr.views import PasswordChangeView, home

urlpatterns = [
    path("", home, name="home"),
    path("", include("hr.urls")),
    path("accounts/login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password/", PasswordChangeView.as_view(), name="password_change"),
    path("accounts/password/done/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    # No password-reset-by-email: no mail server yet; HRMO issues one-time passwords instead.
    path("admin/", admin.site.urls),
]

