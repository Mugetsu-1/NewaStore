"""Payment gateway integration layer (eSewa + Nay Bank Transfer).

Keeps raw HTTP/crypto calls out of views so the checkout and callback handlers
stay thin and unit-testable. Payment methods fail closed when their required
production configuration is absent.
"""

import base64
import hashlib
import hmac
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings


def is_esewa_configured():
    return bool(
        settings.ESEWA_PRODUCT_CODE
        and settings.ESEWA_SECRET_KEY
        and settings.ESEWA_FORM_URL
        and settings.ESEWA_STATUS_URL
    )


BANK_TRANSFER_DETAILS = {
    'bank_name': settings.BANK_TRANSFER_BANK_NAME,
    'account_name': settings.BANK_TRANSFER_ACCOUNT_NAME,
    'account_number': settings.BANK_TRANSFER_ACCOUNT_NUMBER,
    'branch': settings.BANK_TRANSFER_BRANCH,
    'swift': settings.BANK_TRANSFER_SWIFT,
    'instructions': (
        'Transfer the exact order total to the account below, then reply to your '
        'order confirmation email with the deposit slip. Your order is confirmed '
        'as soon as the payment is verified (usually within 1 business day).'
    ),
}


def _esewa_signature(message):
    """Base64(HMAC-SHA256) over `message` using the eSewa secret key."""
    digest = hmac.new(
        settings.ESEWA_SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode()


def build_esewa_form(order, success_url, failure_url):
    """Signed field set for the eSewa ePay v2 auto-submit form.

    The order total already includes tax, so it is sent as the whole
    ``total_amount`` with zeroed tax/charges to avoid taxing twice. The
    signature covers exactly ``total_amount,transaction_uuid,product_code``.
    """
    total_amount = f'{order.total:.2f}'
    transaction_uuid = order.order_number
    product_code = settings.ESEWA_PRODUCT_CODE
    message = (
        f'total_amount={total_amount},'
        f'transaction_uuid={transaction_uuid},'
        f'product_code={product_code}'
    )
    return {
        'action': settings.ESEWA_FORM_URL,
        'fields': {
            'amount': total_amount,
            'tax_amount': '0',
            'total_amount': total_amount,
            'transaction_uuid': transaction_uuid,
            'product_code': product_code,
            'product_service_charge': '0',
            'product_delivery_charge': '0',
            'success_url': success_url,
            'failure_url': failure_url,
            'signed_field_names': 'total_amount,transaction_uuid,product_code',
            'signature': _esewa_signature(message),
        },
    }


def verify_esewa_signature(data):
    """Constant-time check of an eSewa response payload's own signature.

    ``data`` is the decoded ``?data=`` JSON from the success callback. eSewa
    signs the exact string values it returns, so the signed message is rebuilt
    verbatim from the response's own ``signed_field_names`` order.
    """
    if not isinstance(data, dict):
        return False
    field_names = [f.strip() for f in str(data.get('signed_field_names', '')).split(',') if f.strip()]
    required = {
        'transaction_code', 'status', 'total_amount', 'transaction_uuid',
        'product_code', 'signed_field_names',
    }
    if len(field_names) != len(set(field_names)) or set(field_names) != required:
        return False
    if any(not isinstance(data.get(name), str) for name in required):
        return False
    message = ','.join(f'{name}={data.get(name, "")}' for name in field_names)
    expected = _esewa_signature(message)
    supplied = data.get('signature', '')
    return isinstance(supplied, str) and hmac.compare_digest(expected, supplied)


def verify_esewa_status(order):
    """Confirm a callback against eSewa's server-to-server status endpoint."""
    if not getattr(settings, 'ESEWA_VERIFY_STATUS', False):
        return True
    try:
        response = requests.get(
            settings.ESEWA_STATUS_URL,
            params={
                'product_code': settings.ESEWA_PRODUCT_CODE,
                'total_amount': f'{order.total:.2f}',
                'transaction_uuid': order.order_number,
            },
            timeout=10,
            allow_redirects=False,
        )
        if response.status_code != 200:
            return False
        payload = response.json()
        if not isinstance(payload, dict):
            return False
        amount = Decimal(str(payload.get('total_amount', '0')).replace(',', ''))
    except (requests.RequestException, ValueError, TypeError, InvalidOperation):
        return False
    return (
        payload.get('status') == 'COMPLETE'
        and payload.get('transaction_uuid') == order.order_number
        and payload.get('product_code') == settings.ESEWA_PRODUCT_CODE
        and amount == order.total.quantize(Decimal('0.01'))
    )


def available_payment_methods():
    """Checkout radio choices as (value, label, disabled) tuples.

    Unconfigured methods are omitted from the server-validated form choices.
    """
    return [
        ('esewa', 'eSewa', not is_esewa_configured()),
        ('nay_bank', 'Nay Bank Transfer', not settings.BANK_TRANSFER_ENABLED),
    ]


def _available_payment_method_choices():
    """Form-compatible version: only selectable (enabled) gateways become choices."""
    return [(v, l) for (v, l, disabled) in available_payment_methods() if not disabled]
