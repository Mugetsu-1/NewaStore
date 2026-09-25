"""Payment gateway integration layer (eSewa + Nay Bank Transfer).

Keeps raw HTTP/crypto calls out of views so the checkout and callback handlers
stay thin and unit-testable. eSewa degrades gracefully when it is not configured
(empty keys in settings); Nay Bank Transfer settles offline and is always on.
"""

import base64
import hashlib
import hmac

from django.conf import settings


def is_esewa_configured():
    return bool(settings.ESEWA_PRODUCT_CODE and settings.ESEWA_SECRET_KEY)


BANK_TRANSFER_DETAILS = {
    'bank_name': 'Nay Bank',
    'account_name': 'NewaStore Pvt. Ltd.',
    'account_number': '0123456789012',
    'branch': 'Kathmandu — New Road',
    'swift': 'NAYBNPKA',
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
    field_names = [f.strip() for f in data.get('signed_field_names', '').split(',') if f.strip()]
    if not field_names:
        return False
    message = ','.join(f'{name}={data.get(name, "")}' for name in field_names)
    expected = _esewa_signature(message)
    return hmac.compare_digest(expected, data.get('signature', ''))


def available_payment_methods():
    """Checkout radio choices as (value, label, disabled) tuples.

    eSewa disables itself when unconfigured so the checkout UI still lists it.
    Nay Bank Transfer settles offline and is always available.
    """
    return [
        ('esewa', 'eSewa', not is_esewa_configured()),
        ('nay_bank', 'Nay Bank Transfer', False),
    ]


def _available_payment_method_choices():
    """Form-compatible version: only selectable (enabled) gateways become choices."""
    return [(v, l) for (v, l, disabled) in available_payment_methods() if not disabled]
