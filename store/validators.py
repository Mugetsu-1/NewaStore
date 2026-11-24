"""Reusable validators for account, checkout, and contact data.

Enforces the store's data-entry rules in one place so every form agrees:

* Email  -> must be a well-formed Gmail address (``@gmail.com``). Django's
  ``EmailField`` still checks general email syntax; these helpers add the
  domain restriction and normalise the value to lowercase.
* Phone  -> must be a Nepal mobile number (NTC / Ncell / Smart Cell): ten
  digits beginning ``9`` followed by ``6``, ``7`` or ``8``. An optional
  ``+977`` / ``977`` country code and any spaces, dashes or brackets are
  stripped before validation, and the bare ten-digit number is returned.

Password strength is handled separately by ``AUTH_PASSWORD_VALIDATORS`` in
``settings.py`` (applied by every auth form: registration, password change,
and password reset), so there is deliberately no password helper here.
"""

import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

from .security import validate_artwork_url

GMAIL_DOMAIN = "gmail.com"

# Nepal mobile numbers: 10 digits, "9" + {6,7,8} + 8 more.
#   98x -> NTC / Ncell GSM, 97x -> NTC / Ncell, 96x -> Smart Cell / UTL.
_NEPALI_MOBILE_RE = re.compile(r"^9[678]\d{8}$")
_ANALYTICS_ID_RE = re.compile(r"^(?:G-[A-Z0-9]{6,20}|UA-\d{4,12}-\d{1,4})$")

MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
IMAGE_EXTENSION_VALIDATOR = FileExtensionValidator(
    allowed_extensions=("jpg", "jpeg", "png", "webp", "gif")
)


def normalize_email(value):
    """Trim surrounding whitespace and lowercase an email address."""
    return (value or "").strip().lower()


def validate_gmail(value):
    """Return a normalised Gmail address or raise ``ValidationError``.

    Empty input is passed through untouched so callers can decide whether a
    field is required; ``EmailField``/``required`` handle emptiness upstream.
    """
    email = normalize_email(value)
    if not email:
        return email
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain != GMAIL_DOMAIN:
        raise ValidationError(
            "Enter a valid Gmail address — it must end with @gmail.com."
        )
    return email


def normalize_nepali_mobile(value):
    """Strip formatting and any +977 / 977 country code to bare digits."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 13 and digits.startswith("977"):
        digits = digits[3:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def validate_nepali_mobile(value):
    """Return a bare 10-digit Nepal mobile number or raise ``ValidationError``.

    Empty input is passed through so ``required`` controls whether a phone is
    mandatory on a given form.
    """
    if not (value or "").strip():
        return ""
    digits = normalize_nepali_mobile(value)
    if not _NEPALI_MOBILE_RE.match(digits):
        raise ValidationError(
            "Enter a valid Nepali mobile number — 10 digits starting with 98, "
            "97 or 96 (for example 9841234567)."
        )
    return digits


def validate_image_upload(value):
    """Bound image upload size and reject non-image MIME declarations."""
    if not value:
        return
    size = getattr(value, "size", 0) or 0
    if size > MAX_IMAGE_UPLOAD_BYTES:
        raise ValidationError("Images must be 5 MB or smaller.")
    content_type = (getattr(value, "content_type", "") or "").lower()
    if content_type and content_type not in {
        "image/jpeg", "image/png", "image/webp", "image/gif"
    }:
        raise ValidationError("Upload a JPEG, PNG, WebP, or GIF image.")


def validate_analytics_id(value):
    value = (value or "").strip().upper()
    if value and not _ANALYTICS_ID_RE.fullmatch(value):
        raise ValidationError("Enter a valid Google Analytics measurement ID.")


def validate_single_line(value):
    if "\r" in (value or "") or "\n" in (value or ""):
        raise ValidationError("Line breaks are not allowed in this field.")


def validate_https_url(value):
    if not value:
        return
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid HTTPS URL.") from exc
    if (
        parsed.scheme.lower() != 'https' or not parsed.hostname
        or parsed.username or parsed.password or port not in (None, 443)
    ):
        raise ValidationError("Use an HTTPS URL without embedded credentials.")
