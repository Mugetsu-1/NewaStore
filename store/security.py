"""Shared security helpers for HTTP views, authentication and outbound URLs."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.http import HttpResponse, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme


DEFAULT_ARTWORK_HOSTS = {
    "shared.fastly.steamstatic.com",
    "shared.akamai.steamstatic.com",
    "cdn.cloudflare.steamstatic.com",
    "cdn.akamai.steamstatic.com",
}


def _normalise_host(host: str) -> str:
    return (host or "").strip().lower().rstrip(".")


def artwork_allowed_hosts() -> set[str]:
    configured = getattr(settings, "ARTWORK_ALLOWED_HOSTS", ())
    normalised = set()
    for value in configured:
        host = _normalise_host(value)
        if not re.fullmatch(r'[a-z0-9.-]+', host) or '..' in host:
            raise ImproperlyConfigured(f'Invalid ARTWORK_ALLOWED_HOSTS entry: {value}')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if host == 'localhost' or host.endswith('.local') or (
            address is not None and not address.is_global
        ):
            raise ImproperlyConfigured(f'Unsafe ARTWORK_ALLOWED_HOSTS entry: {value}')
        normalised.add(host)
    return DEFAULT_ARTWORK_HOSTS | normalised


def validate_artwork_url(value: str) -> None:
    """Reject artwork URLs that could turn the fetch pipeline into SSRF.

    Artwork is imported only from a small set of trusted HTTPS CDNs. Redirects
    are validated again by the downloader, so an allowed host cannot redirect a
    worker to localhost, cloud metadata, or an arbitrary internet host.
    """

    if not value:
        return
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid artwork URL.") from exc

    host = _normalise_host(parsed.hostname or "")
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or host not in artwork_allowed_hosts()
        or host == 'localhost'
        or host.endswith('.local')
        or (literal_ip is not None and not literal_ip.is_global)
    ):
        raise ValidationError(
            "Artwork must use HTTPS on an approved Steam CDN host."
        )


def get_client_ip(request) -> str:
    """Return a validated client IP without trusting spoofable headers by default."""

    candidates: list[str] = []
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        # A trusted edge proxy appends the address it observed to the right. The
        # right-most valid value therefore cannot be selected by the client.
        candidates.extend(
            reversed(
                [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")]
            )
        )
    candidates.append((request.META.get("REMOTE_ADDR") or "").strip())

    for candidate in candidates:
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return "unknown"


def safe_redirect_target(request, fallback: str) -> str:
    """Use Referer for convenience only when it is a same-host safe URL."""

    candidate = request.META.get("HTTP_REFERER", "")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def _rate_key(scope: str, identity: str) -> str:
    digest = hashlib.sha256(
        f"{settings.SECRET_KEY}|{scope}|{identity}".encode("utf-8", "replace")
    ).hexdigest()
    return f"rate:{scope}:{digest}"


def consume_rate_limit(request, scope: str, limit: int, window: int) -> tuple[bool, int]:
    """Consume one fixed-window allowance and return ``(allowed, retry_after)``."""

    key = _rate_key(scope, get_client_ip(request))
    if cache.add(key, 1, timeout=window):
        return True, 0
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)
        count = 1
    if count <= limit:
        return True, 0
    # Django's cache API does not expose portable remaining TTL. Returning the
    # full window is conservative and avoids leaking backend-specific details.
    return False, window


def rate_limit(scope: str, limit: int, window: int, methods=("POST",)):
    """Small cache-backed rate-limit decorator for abuse-prone HTML endpoints."""

    methods = {method.upper() for method in methods}

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method.upper() in methods:
                allowed, retry_after = consume_rate_limit(
                    request, scope=scope, limit=limit, window=window
                )
                if not allowed:
                    if request.headers.get("x-requested-with") == "XMLHttpRequest":
                        response = JsonResponse(
                            {"success": False, "message": "Too many requests. Please try again later."},
                            status=429,
                        )
                    else:
                        response = HttpResponse(
                            "Too many requests. Please try again later.",
                            status=429,
                            content_type="text/plain; charset=utf-8",
                        )
                    response["Retry-After"] = str(retry_after)
                    return response
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
