"""Django pages for email sign-in and passwords (fallback/testing UI; cp-web uses the API):

    /dj/auth/email/                    ask for a mail (?purpose=reset for "forgot password", ?role=organizer)
    /dj/auth/email/code/<challenge>/   enter the mail's code
    /dj/auth/email/<token>/            the mail's link: a button that signs in (mail scanners only GET links,
                                       so the GET itself must not use up the link), then on to cp-web:
                                       CP_WEB_SET_PASSWORD_PATH (/set-password) or CP_WEB_HOME_PATH (/)
    /dj/auth/password/                 choose a password (signed in)
"""
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from common.api import ApiError

from . import emailauth
from .backends import SESSION_BACKEND
from .models import EmailChallenge


def _signed_in(request, user, event, purpose, uploader_entry=False):
    login(request, user, backend=SESSION_BACKEND)
    emailauth.after_sign_in(request, event, may_set_password=True, uploader_entry=uploader_entry)
    if emailauth.needs_password(user, purpose):
        return redirect("password")
    if event is not None:
        return redirect("participant:event", code=event.display_code)
    if not user.is_consent:   # KVKK first
        return redirect("participant:consent")
    return redirect("home")


def email_start(request):
    purpose = request.GET.get("purpose") or request.POST.get("purpose") or "sign_in"
    role = request.GET.get("role") or request.POST.get("role") or "participant"
    error, email, registered = "", "", False
    if request.method == "POST":
        email = request.POST.get("email", "")
        try:
            challenge = emailauth.request_mail(email, purpose, role, None, request.POST.get("locale", "tr"),
                                               request.META.get("REMOTE_ADDR"), request.build_absolute_uri("/"))
            return redirect("email_code", challenge_id=challenge.pk)
        except ApiError as e:
            error, registered = e.message, e.code == "email_already_registered"
    return render(request, "accounts/email_start.html",
                  {"purpose": purpose, "role": role, "email": email, "error": error, "registered": registered})


def email_code(request, challenge_id):
    challenge = EmailChallenge.objects.filter(pk=challenge_id).first()
    error = ""
    if request.method == "POST":
        try:
            user, _, event, purpose, up = emailauth.verify(challenge_id=challenge_id, code=request.POST.get("code", ""))
            return _signed_in(request, user, event, purpose, up)
        except ApiError as e:
            error = e.message
    return render(request, "accounts/email_code.html", {"challenge": challenge, "error": error})


def email_link(request, token):
    error = ""
    if request.method == "POST":
        try:
            user, _, event, purpose, up = emailauth.verify(token=token)
            # From a mail: on to cp-web (the customer-facing pages), not the Django test pages.
            login(request, user, backend=SESSION_BACKEND)
            emailauth.after_sign_in(request, event, may_set_password=True, uploader_entry=up)
            if emailauth.needs_password(user, purpose):
                return redirect(settings.WEB_SET_PASSWORD_PATH)
            return redirect(settings.WEB_HOME_PATH)
        except ApiError as e:
            error = e.message
    return render(request, "accounts/email_link.html", {"error": error})


@login_required
def password(request):
    user = request.user
    if user.is_guest:
        return redirect("home")
    error = ""
    needs_current = user.has_usable_password() and not emailauth.in_password_window(request)
    if request.method == "POST":
        try:
            emailauth.set_password(request, request.POST.get("password", ""), request.POST.get("current_password"))
            if not user.is_consent:   # KVKK first
                return redirect("participant:consent")
            return redirect("home")
        except ApiError as e:
            error = e.message
    return render(request, "accounts/password.html", {"needs_current": needs_current, "error": error,
                                                      "min_length": emailauth.settings.PASSWORD_MIN_LENGTH})
