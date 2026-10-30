"""Optional modules, switched on in `HR_MODULES` (DECISIONS MD1-MD3).

Features built ahead of a request from HRMO stay off until HRMO asks for them: a module that is off is
missing from the menus, the HR Desk and the person's record, and its pages answer "not found". Its
code, data and tests stay, so switching it on is a settings change.
"""

from functools import wraps

from django.conf import settings
from django.http import Http404

OPTIONAL = {
    "pass_slips": "Pass slips",
    "development_plans": "Individual Development Plans",
    "awards": "Awards and recognition",
    "workforce": "Workforce profile",
    "official_email": "Official email register",
}


def enabled(name):
    if name not in OPTIONAL:
        raise ValueError(f"Unknown module: {name}")
    return name in settings.HR_MODULES


def requires(name, view):
    """The view, answering 404 while the module is off. Used in urls.py."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not enabled(name):
            raise Http404(f"{OPTIONAL[name]} is not switched on.")
        return view(request, *args, **kwargs)

    return wrapped


def flags():
    """{module: on?} for templates."""
    return {name: name in settings.HR_MODULES for name in OPTIONAL}
