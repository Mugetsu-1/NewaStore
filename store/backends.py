import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import LoginThrottle
from .security import get_client_ip


logger = logging.getLogger("store.security")


def _digest(kind, value):
    return salted_hmac(
        "newastore.login-throttle",
        f"{kind}:{value}",
        secret=settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


class EmailOrUsernameModelBackend(ModelBackend):
    """Authenticate by username/email and rate-limit failures in PostgreSQL."""

    def _keys(self, request, identifier):
        ip = get_client_ip(request) if request is not None else "unknown"
        normalized = (identifier or "").strip().casefold()[:254]
        return {
            _digest("pair", f"{ip}|{normalized}"): int(
                getattr(settings, "AUTH_PAIR_FAILURE_LIMIT", 5)
            ),
            _digest("ip", ip): int(getattr(settings, "AUTH_IP_FAILURE_LIMIT", 25)),
        }

    def _blocked(self, keys):
        now = timezone.now()
        return LoginThrottle.objects.filter(
            key__in=keys, blocked_until__gt=now
        ).exists()

    def _record_failure(self, keys):
        now = timezone.now()
        window = timedelta(seconds=int(getattr(settings, "AUTH_FAILURE_WINDOW", 900)))
        block_for = timedelta(seconds=int(getattr(settings, "AUTH_BLOCK_DURATION", 900)))

        if cache.add('auth-throttle-cleanup', 1, timeout=3600):
            LoginThrottle.objects.filter(updated_at__lt=now - timedelta(days=7)).delete()

        for key, limit in keys.items():
            for attempt in range(2):
                try:
                    with transaction.atomic():
                        try:
                            row = LoginThrottle.objects.select_for_update().get(key=key)
                        except LoginThrottle.DoesNotExist:
                            row = LoginThrottle(
                                key=key, failures=0, window_started_at=now
                            )
                        if row.window_started_at < now - window:
                            row.failures = 0
                            row.window_started_at = now
                            row.blocked_until = None
                        row.failures += 1
                        if row.failures >= max(1, limit):
                            row.blocked_until = now + block_for
                        row.save()
                    break
                except IntegrityError:
                    if attempt:
                        logger.warning("Could not update an authentication throttle row")

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = username or kwargs.get(User.USERNAME_FIELD)
        if not identifier or password is None:
            return None

        keys = self._keys(request, identifier)
        if self._blocked(keys):
            # Deliberately return the same result as invalid credentials. Views
            # must not reveal whether a user exists or a throttle is active.
            logger.warning("Blocked a rate-limited authentication attempt")
            return None

        users = list(
            User.objects.filter(
                Q(username__iexact=identifier) | Q(email__iexact=identifier)
            ).distinct()[:2]
        )
        if len(users) != 1:
            # Match the work factor of a real password check and fail closed on
            # ambiguous duplicate emails rather than selecting an arbitrary user.
            User().set_password(password)
            self._record_failure(keys)
            return None

        user = users[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            pair_key = next(iter(keys))
            LoginThrottle.objects.filter(key=pair_key).delete()
            return user

        self._record_failure(keys)
        return None
