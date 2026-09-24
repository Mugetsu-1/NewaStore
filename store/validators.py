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

from django.core.exceptions import ValidationError

GMAIL_DOMAIN = "gmail.com"

# Nepal mobile numbers: 10 digits, "9" + {6,7,8} + 8 more.
#   98x -> NTC / Ncell GSM, 97x -> NTC / Ncell, 96x -> Smart Cell / UTL.
_NEPALI_MOBILE_RE = re.compile(r"^9[678]\d{8}$")


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
