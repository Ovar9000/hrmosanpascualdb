from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from hr import account_views, mfa_views
from hr.views import PasswordChangeView, home

urlpatterns = [
    path("", home, name="home"),
    path("", include("hr.urls")),
    path("accounts/login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password/", PasswordChangeView.as_view(), name="password_change"),
    path("accounts/password/done/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    # No password-reset-by-email: no mail server yet. "Forgot password?" asks HRMO, who checks
    # an ID and issues a one-time password (DECISIONS A2).
    # Second login step with an authenticator app, no email or SMS (DECISIONS A5).
    path("accounts/two-step/", mfa_views.mfa_settings, name="mfa_settings"),
    path("accounts/two-step/code/", mfa_views.mfa_verify, name="mfa_verify"),
    path("accounts/two-step/setup/", mfa_views.mfa_setup, name="mfa_setup"),
    path("accounts/forgot/", account_views.forgot_password, name="forgot_password"),
    path("accounts/forgot/sent/", account_views.forgot_password_sent, name="forgot_password_sent"),
    path("accounts/reset-requests/", account_views.reset_request_list, name="reset_request_list"),
    path("accounts/reset-requests/<int:pk>/", account_views.reset_request_detail, name="reset_request_detail"),
    path("people/<int:pk>/password/", account_views.person_password_reset, name="person_password_reset"),
    path("admin/", admin.site.urls),
]

