"""Second login step: set up the authenticator app, type a code at login, manage it (DECISIONS A5)."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from . import mfa
from .models import MfaDevice


def _next_url(request):
    url = request.POST.get("next") or request.GET.get("next") or ""
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return settings.LOGIN_REDIRECT_URL


# The app shows a code for 30 seconds, so logging out and straight back in brings up the one just used.
CODE_USED = "That code was just used. Wait for the app to show a new one (up to 30 seconds), then type that."
# Codes that keep failing usually mean the phone's clock is off: they are worked out from the time.
CLOCK_HINT = "Still not working? Set the phone's date and time to automatic."


def _clock_off(offset):
    """For a right code from a clock that is off (mfa.clock_offset): says which way, and what to fix."""
    amount = f"{abs(offset)} seconds" if abs(offset) < 120 else f"{round(abs(offset) / 60)} minutes"
    way = "ahead of" if offset > 0 else "behind"
    return (f"That code is right, but for a time {amount} {way} this server's clock, so it cannot be accepted. "
            "Set the phone's date and time to automatic. If it already is, this server's clock is wrong: "
            "tell the system administrator.")


def _no_store(response):
    response["Cache-Control"] = "no-store"
    return response


def _show_recovery_codes(request, codes):
    """The one time the codes are ever shown. Not cached, so Back does not bring them up again."""
    return _no_store(render(request, "registration/mfa_recovery_codes.html", {"codes": codes}))


@login_required
def mfa_verify(request):
    if not mfa.has_device(request.user):
        return redirect("mfa_setup")
    if mfa.is_verified(request):
        return redirect(_next_url(request))
    if mfa.is_remembered(request):
        mfa.pass_remembered(request)
        return redirect(_next_url(request))
    error = None
    if request.method == "POST":
        try:
            method = mfa.check_code(request.user, request.POST.get("code"))
        except mfa.CodeUsed:
            method, error = None, CODE_USED
        except mfa.ClockOff as off:
            method, error = None, _clock_off(off.offset)
        except mfa.Locked:
            logout(request)
            messages.error(request, f"Too many wrong codes. Try again in {mfa.LOCK_MINUTES} minutes.")
            return redirect("login")
        if method:
            mfa.mark_verified(request)
            if method == "recovery code":
                left = mfa.recovery_codes_left(request.user)
                messages.warning(request, f"Recovery code used. {left} left: set up the app again or make new codes "
                                          "under Two-step login.")
            response = redirect(_next_url(request))
            if request.POST.get("remember"):
                mfa.remember_browser(response, request.user)
            return response
        error = error or f"That code is not right. Check the app shows San Pascual HR and type the newest code. {CLOCK_HINT}"
    return render(request, "registration/mfa_verify.html", {
        "error": error, "next": _next_url(request), "remember_days": settings.MFA_REMEMBER_DAYS,
    })


@login_required
def mfa_setup(request):
    """First setup, forced at login for the roles that need it, or chosen from Two-step login."""
    if mfa.has_device(request.user):
        return redirect("mfa_settings" if mfa.is_verified(request) else "mfa_verify")
    secret = request.session.get(mfa.SESSION_PENDING_SECRET)
    if not secret:
        secret = request.session[mfa.SESSION_PENDING_SECRET] = mfa.new_secret()
    error = None
    if request.method == "POST":
        codes = mfa.enroll(request.user, secret, request.POST.get("code"))
        if codes:
            mfa.mark_verified(request)
            response = _show_recovery_codes(request, codes)
            if request.POST.get("remember"):
                mfa.remember_browser(response, request.user)
            return response
        offset = mfa.clock_offset(secret, request.POST.get("code"))
        error = _clock_off(offset) if offset is not None else (
            f"That code is not right. Scan the picture again, or type the key, and use the newest code. {CLOCK_HINT}"
        )
    uri = mfa.provisioning_uri(secret, request.user.get_username())
    return _no_store(render(request, "registration/mfa_setup.html", {
        "qr": mfa.qr_data_uri(uri), "secret": mfa.grouped(secret), "error": error,
        "required": mfa.mfa_required_for(request.user), "remember_days": settings.MFA_REMEMBER_DAYS,
    }))


@login_required
def mfa_settings(request):
    """New recovery codes or turning it off (both need a code), or forgetting remembered browsers."""
    device = MfaDevice.objects.filter(user=request.user).first()
    required = mfa.mfa_required_for(request.user)
    error = None
    if request.method == "POST" and device and request.POST.get("action") == "forget":
        # No code needed: forgetting only ever makes the account safer.
        mfa.forget_browsers(request.user)
        messages.success(request, "Every remembered browser, this one too, will ask for a code at the next login.")
        response = redirect("mfa_settings")
        response.delete_cookie(mfa.remember_cookie_name(request.user), samesite="Lax")
        return response
    if request.method == "POST" and device:
        action = request.POST.get("action")
        try:
            ok = mfa.check_code(request.user, request.POST.get("code"), allow_recovery=False)
        except mfa.CodeUsed:
            ok, error = None, CODE_USED
        except mfa.ClockOff as off:
            ok, error = None, _clock_off(off.offset)
        except mfa.Locked:
            logout(request)
            messages.error(request, f"Too many wrong codes. Try again in {mfa.LOCK_MINUTES} minutes.")
            return redirect("login")
        if not ok:
            error = error or f"That code is not right. Type the newest code from the app. {CLOCK_HINT}"
        elif action == "codes":
            return _show_recovery_codes(request, mfa.replace_recovery_codes(request.user))
        elif action == "off" and not required:
            mfa.reset(request.user, "Turned off by the account holder", actor=request.user)
            messages.success(request, "Two-step login is off.")
            return redirect("mfa_settings")
    return render(request, "registration/mfa_settings.html", {
        "device": device, "required": required, "error": error,
        "codes_left": mfa.recovery_codes_left(request.user) if device else 0,
        "remember_days": settings.MFA_REMEMBER_DAYS, "remembered": bool(device) and mfa.is_remembered(request),
    })
