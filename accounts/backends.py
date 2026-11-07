"""Password sign-in. The identifier is a phone or an email (anything with "@" is an email), at
/dj/login/ and /admin/ (and the password sign-in API).

- IdentifierBackend: the identifier + the account's own password.
- MasterPassBackend: the identifier + CP_MASTER_PASS signs in to any active account (non-production;
  off when MASTER_PASS is empty)."""
import logging

from django.contrib.auth.backends import ModelBackend

from .models import User, normalize_email
from .otp import is_master_pass

log = logging.getLogger("cp.auth")

# What login() records in the session for sign-ins without a password (OTP, email link/code).
SESSION_BACKEND = "accounts.backends.IdentifierBackend"


def find_user(identifier: str | None) -> User | None:
    """The account a typed phone or email names, or None."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    if "@" in identifier:
        return User.objects.filter(email=normalize_email(identifier)).first()
    return User.objects.filter(phone=identifier).first()


class IdentifierBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or password is None:
            return None
        user = find_user(username)
        if user is None:
            User().set_password(password)   # same work as a real check: no timing hint that the account is missing
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None


class MasterPassBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or not is_master_pass(password):
            return None
        user = find_user(username)
        if user is None or not self.user_can_authenticate(user):
            return None
        log.warning("master pass login: %s", user.label)
        return user
