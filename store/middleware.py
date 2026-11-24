"""Application security middleware kept independent from business views."""

from __future__ import annotations

import ipaddress
import secrets
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, HttpResponseNotFound

from .security import artwork_allowed_hosts, consume_rate_limit, get_client_ip


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return ""
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if parsed.port and parsed.port != 443:
        host = f"{host}:{parsed.port}"
    return f"https://{host}"


class AdminAccessMiddleware:
    """Optionally restrict the admin surface to configured IP networks."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.prefix = "/" + settings.ADMIN_URL.strip("/") + "/"
        self.networks = []
        for value in getattr(settings, "ADMIN_ALLOWED_NETWORKS", ()):
            try:
                self.networks.append(ipaddress.ip_network(value, strict=False))
            except ValueError as exc:
                raise ImproperlyConfigured(
                    f'Invalid DJANGO_ADMIN_ALLOWED_NETWORKS entry: {value}'
                ) from exc

    def __call__(self, request):
        login_paths = {'/login/', self.prefix + 'login/'}
        if request.method == 'POST' and request.path in login_paths:
            allowed, retry_after = consume_rate_limit(
                request,
                'authentication-requests',
                int(getattr(settings, 'AUTH_REQUEST_LIMIT', 20)),
                int(getattr(settings, 'AUTH_REQUEST_WINDOW', 60)),
            )
            if not allowed:
                response = HttpResponse(
                    'Too many requests', status=429,
                    content_type='text/plain; charset=utf-8',
                )
                response['Retry-After'] = str(retry_after)
                return response
        if self.networks and request.path.startswith(self.prefix):
            try:
                address = ipaddress.ip_address(get_client_ip(request))
            except ValueError:
                address = None
            if address is None or not any(address in network for network in self.networks):
                # A 404 discloses less about the administration surface than a
                # permission response while still being easy to diagnose in logs.
                return HttpResponseNotFound("Not found")
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Attach a nonce-based CSP and modern browser isolation headers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(18)
        response = self.get_response(request)

        is_admin = request.path.startswith("/" + settings.ADMIN_URL.strip("/") + "/")
        esewa_origin = _origin(getattr(settings, "ESEWA_FORM_URL", ""))
        form_sources = ["'self'"] + ([esewa_origin] if esewa_origin else [])
        image_sources = ["'self'", 'data:'] + [
            f'https://{host}' for host in sorted(artwork_allowed_hosts())
        ]

        if is_admin:
            # Django admin/Jazzmin ship trusted inline initialization snippets.
            # The public storefront remains strict and nonce-only below.
            script_sources = "'self' 'unsafe-inline'"
            style_sources = "'self' 'unsafe-inline'"
            font_sources = "'self' data:"
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        else:
            script_sources = (
                f"'self' 'nonce-{request.csp_nonce}' "
                "https://www.googletagmanager.com"
            )
            style_sources = "'self' 'unsafe-inline' https://cdnjs.cloudflare.com"
            font_sources = "'self' data: https://cdnjs.cloudflare.com"

        directives = [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "frame-src 'none'",
            f"form-action {' '.join(form_sources)}",
            f"script-src {script_sources}",
            f"style-src {style_sources}",
            f"font-src {font_sources}",
            f"img-src {' '.join(image_sources)}",
            "connect-src 'self' https://www.google-analytics.com https://region1.google-analytics.com",
            "media-src 'self'",
            "worker-src 'none'",
        ]
        if not is_admin:
            directives.append("script-src-attr 'none'")
        if not settings.DEBUG:
            directives.append("upgrade-insecure-requests")

        response["Content-Security-Policy"] = "; ".join(directives)
        response["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
            "browsing-topics=()"
        )
        response["X-Permitted-Cross-Domain-Policies"] = "none"
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")

        sensitive_prefixes = (
            "/checkout/", "/payment/", "/order/", "/orders/", "/profile/",
            "/password-reset/", "/verify-email/",
            "/" + settings.ADMIN_URL.strip("/") + "/",
        )
        if request.path.startswith(sensitive_prefixes):
            response["Cache-Control"] = "no-store, max-age=0"
            response["Pragma"] = "no-cache"
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            response["Referrer-Policy"] = "no-referrer"
        return response
