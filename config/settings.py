"""Settings for the San Pascual HR prototype.

All secrets and environment-specific values come from environment variables
(see .env.example). Security choices are explained inline and in README.md.
"""

import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default="0"):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


# No default: the app refuses to start without a secret key.
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = env_bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "hr",
]

MIDDLEWARE = [
    # First: answers the host's /healthz/ probe before the host check and HTTPS redirect.
    "hr.middleware.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Serves /static/ from STATIC_ROOT (collected in the Docker image), no proxy needed.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "hr.middleware.AuditMiddleware",
    "hr.middleware.MustChangePasswordMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must be last: turns locked-out logins into a lockout response.
    "axes.middleware.AxesMiddleware",
]

# Trusted reverse proxies for client IP extraction from X-Forwarded-For (DECISIONS U5)
TRUSTED_PROXIES = env_list("TRUSTED_PROXIES", "127.0.0.1,::1")
# Optional header a trusted proxy sets to the client's address (Railway: X-Real-IP). When set,
# it is used instead of walking X-Forwarded-For, which on Railway can end in a CDN hop.
CLIENT_IP_HEADER = os.environ.get("CLIENT_IP_HEADER", "").strip()

# Prototype data guard (DECISIONS D1): form-level check that ID numbers start with 00.
# Turning this off does NOT allow real IDs: the person_*_fake_only DB constraints remain.
PROTOTYPE_FAKE_DATA_ONLY = env_bool("PROTOTYPE_FAKE_DATA_ONLY", "1")

# Employee age constraints (DECISIONS S10)
MIN_EMPLOYEE_AGE = int(os.environ.get("MIN_EMPLOYEE_AGE", "18"))
MAX_EMPLOYEE_AGE = int(os.environ.get("MAX_EMPLOYEE_AGE", "90"))


ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "hr.context_processors.org_chart_nav",
                "hr.context_processors.board_nav",
            ],
        },
    },
]

# DB_USER is the restricted app role for the website and the owner role for the
# "manage" service (migrations, tests). See docker-compose.yml.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("DB_HOST", "db"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "CONN_MAX_AGE": 60,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Authentication -----------------------------------------------------------
# Passwords: Django's default PBKDF2-SHA256 hasher (salted, ~1M iterations in 5.2).
# No custom hasher: the default is vetted and upgrades itself on login.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Axes first so locked-out users are rejected before the password is even checked.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    # ModelBackend that also refuses expired one-time passwords (DECISIONS A4).
    "hr.backends.OneTimePasswordBackend",
]

# Days an unused one-time password stays valid (provision_accounts, admin resets).
ONE_TIME_PASSWORD_DAYS = int(os.environ.get("ONE_TIME_PASSWORD_DAYS", "14"))

# Salary actions tracker (DECISIONS SA1): the go-live date. Milestones before it are assumed handled
# on paper, so the tracker starts without a backlog; cases open this many days before they fall due.
SALARY_TRACKING_START = os.environ.get("SALARY_TRACKING_START", "2026-10-01")
SALARY_LOOKAHEAD_DAYS = int(os.environ.get("SALARY_LOOKAHEAD_DAYS", "90"))
# Contract renewals list (DECISIONS RM1): appointments ending within this many days.
RENEWAL_LOOKAHEAD_DAYS = int(os.environ.get("RENEWAL_LOOKAHEAD_DAYS", "45"))
# Leave approval order (DECISIONS L1): the signature boxes of CS Form No. 6, section 7, in the order
# they are signed. Any order of recommend, certify and approve; approve must be one of them.
LEAVE_APPROVAL_STEPS = [
    s.strip() for s in os.environ.get("LEAVE_APPROVAL_STEPS", "recommend,certify,approve").split(",") if s.strip()
]
# Names printed under the signature lines of CS Form No. 6 (DECISIONS L6), as on the LGU's paper form.
# Blank prints an empty line for the name.
LEAVE_FORM_CERTIFIER = os.environ.get("LEAVE_FORM_CERTIFIER", "")
LEAVE_FORM_CERTIFIER_TITLE = os.environ.get("LEAVE_FORM_CERTIFIER_TITLE", "Administrative Officer V")
LEAVE_FORM_MAYOR = os.environ.get("LEAVE_FORM_MAYOR", "")
# Service records and certificates of employment (DECISIONS CR1, CR4). Job order and contract of service
# are not government service, so they are left off the service record (the COE still lists them).
SERVICE_RECORD_EXCLUDED_TYPES = [
    s.strip() for s in os.environ.get("SERVICE_RECORD_EXCLUDED_TYPES", "jo,cos").split(",") if s.strip()
]
# Name and title printed under the "Certified correct" line. Blank prints an empty line for the name.
CERTIFICATE_SIGNATORY = os.environ.get("CERTIFICATE_SIGNATORY", "")
CERTIFICATE_SIGNATORY_TITLE = os.environ.get("CERTIFICATE_SIGNATORY_TITLE", "Municipal Human Resource Management Officer")


def _types(name, default):
    return [s.strip() for s in os.environ.get(name, default).split(",") if s.strip()]


# Licences from the PDS that expire within this many days are listed (DECISIONS EL1).
LICENCE_LOOKAHEAD_DAYS = int(os.environ.get("LICENCE_LOOKAHEAD_DAYS", "90"))
# Learning and development hours each person should reach in a year; 0 shows hours without a target (LD3).
LD_TARGET_HOURS = int(os.environ.get("LD_TARGET_HOURS", "0"))
# Appointment types rated every semester under SPMS (SP1); JO and COS are not employees.
SPMS_RATED_TYPES = _types("SPMS_RATED_TYPES", "plantilla,casual,contractual")
# Appointment types that file a SALN every year (SN1). RA 6713 exempts casual and temporary workers.
SALN_COVERED_TYPES = _types("SALN_COVERED_TYPES", "plantilla,elected")
# The first year whose SALN is tracked here (earlier ones were filed on paper): no reminders before it.
SALN_FIRST_YEAR = int(os.environ.get("SALN_FIRST_YEAR", "2026"))
# Compensatory overtime credits (CT1-CT3): hours credited per hour worked, caps, and how long they last.
COC_EXCLUDED_TYPES = _types("COC_EXCLUDED_TYPES", "jo,cos")
COC_WORKDAY_RATE = os.environ.get("COC_WORKDAY_RATE", "1.0")
COC_RESTDAY_RATE = os.environ.get("COC_RESTDAY_RATE", "1.5")
COC_MONTHLY_CAP = int(os.environ.get("COC_MONTHLY_CAP", "40"))
COC_BALANCE_CAP = int(os.environ.get("COC_BALANCE_CAP", "120"))
COC_VALID_MONTHS = int(os.environ.get("COC_VALID_MONTHS", "12"))

# Login rate limiting: 5 failures for the same username+IP -> 15 min lockout.
# Keyed on the pair so one attacker can't lock a real user out from everywhere.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
# Same client-IP resolver as the audit log: behind a proxy listed in TRUSTED_PROXIES each
# client is keyed on its own address, not the proxy's. Otherwise every user would share the
# proxy IP and 5 bad guesses would lock a username out everywhere (DECISIONS U5).
AXES_CLIENT_IP_CALLABLE = "hr.audit.get_client_ip"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# --- Sessions -----------------------------------------------------------------
# 30 min idle timeout: the expiry is pushed forward on every request, so active
# users stay in; a phone left on a desk logs out. Also ends when the browser closes.
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# --- HTTPS-ready --------------------------------------------------------------
# Off for local http://localhost dev. Set DJANGO_HTTPS=1 once a TLS reverse proxy
# (e.g. Caddy/nginx) sits in front. The proxy must set X-Forwarded-Proto.
if env_bool("DJANGO_HTTPS"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Start at 1 hour; raise to 1 year once HTTPS is confirmed stable.
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "3600"))
X_FRAME_OPTIONS = "DENY"

# --- Locale -------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = False
USE_TZ = True

# --- Files --------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded HR documents. Deliberately NOT exposed via MEDIA_URL: files are only
# served through views that check permissions.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "/data/media"))
MAX_UPLOAD_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE", 5 * 1024 * 1024))  # 5 MB default

