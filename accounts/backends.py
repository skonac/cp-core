"""Login with the master pass (settings.MASTER_PASS, non-production only): phone + master pass
signs in to any active account, at /dj/login/ and /admin/. Off when MASTER_PASS is empty."""
import logging

from django.contrib.auth.backends import ModelBackend

from .models import User
from .otp import is_master_pass

log = logging.getLogger("cp.auth")


class MasterPassBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if not username or not is_master_pass(password):
            return None
        user = User.objects.filter(phone=username).first()
        if user is None or not self.user_can_authenticate(user):
            return None
        log.warning("master pass login: %s", user.phone)
        return user
