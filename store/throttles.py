"""DRF throttles that use the application's validated proxy/client IP rules."""

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle

from .security import get_client_ip


class SecureClientIPMixin:
    def get_ident(self, request):
        return get_client_ip(request)


class SecureAnonRateThrottle(SecureClientIPMixin, AnonRateThrottle):
    """Anonymous API throttle resistant to spoofed X-Forwarded-For values."""


class ContactRateThrottle(SecureClientIPMixin, SimpleRateThrottle):
    scope = 'contact'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}
