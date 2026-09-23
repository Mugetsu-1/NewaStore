"""Payment gateway integration layer (Stripe / PayPal / eSewa / Khalti).

Keeps raw HTTP/SDK calls out of views so the checkout and webhook handlers
stay thin and unit-testable. Every function degrades gracefully when the
gateway is not configured (empty keys in settings).
"""

import base64
import hashlib
import hmac

from django.conf import settings

PAYPAL_API_BASE = {
    True: 'https://api-m.sandbox.paypal.com',
    False: 'https://api-m.paypal.com',
}


class PayPalError(Exception):
    """Raised for any non-success PayPal API response."""



def is_stripe_configured():
    return bool(settings.STRIPE_PUBLIC_KEY and settings.STRIPE_SECRET_KEY)


def is_paypal_configured():
    return bool(settings.PAYPAL_CLIENT_ID and settings.PAYPAL_SECRET)


class KhaltiError(Exception):
    """Raised for any non-success Khalti API response."""


def is_esewa_configured():
    return bool(settings.ESEWA_PRODUCT_CODE and settings.ESEWA_SECRET_KEY)


def is_khalti_configured():
    return bool(settings.KHALTI_SECRET_KEY)


BANK_TRANSFER_DETAILS = {
    'bank_name': 'Nay Bank',
    'account_name': 'Newa Store Pvt. Ltd.',
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


def initiate_khalti_payment(order, return_url, website_url):
    """Server-side KPG-2 initiate. Returns the {pidx, payment_url} dict."""
    import requests
    resp = requests.post(
        settings.KHALTI_INITIATE_URL,
        headers={'Authorization': f'Key {settings.KHALTI_SECRET_KEY}'},
        json={
            'return_url': return_url,
            'website_url': website_url,
            'amount': int(round(order.total * 100)),
            'purchase_order_id': order.order_number,
            'purchase_order_name': f'Newa Store order {order.order_number}',
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise KhaltiError(f'Khalti initiate failed (HTTP {resp.status_code}).')
    return resp.json()


def lookup_khalti_payment(pidx):
    """Authoritative KPG-2 lookup. Returns the payment status dict."""
    import requests
    resp = requests.post(
        settings.KHALTI_LOOKUP_URL,
        headers={'Authorization': f'Key {settings.KHALTI_SECRET_KEY}'},
        json={'pidx': pidx},
        timeout=20,
    )
    if resp.status_code != 200:
        raise KhaltiError(f'Khalti lookup failed (HTTP {resp.status_code}).')
    return resp.json()


def available_payment_methods():
    """Checkout radio choices as (value, label, disabled) tuples.

    Unconfigured gateways stay visible but disabled so the checkout UI looks
    complete before keys are added. Nay Bank Transfer settles offline and is
    always available.
    """
    return [
        ('esewa', 'eSewa', not is_esewa_configured()),
        ('khalti', 'Khalti', not is_khalti_configured()),
        ('nay_bank', 'Nay Bank Transfer', False),
        ('stripe', 'Credit/Debit Card (Stripe)', not is_stripe_configured()),
        ('paypal', 'PayPal', not is_paypal_configured()),
    ]


def _available_payment_method_choices():
    """Form-compatible version: only selectable (enabled) gateways become choices.

    Disabled/unconfigured gateways still mention in the field's help text so the
    user sees they exist but are not turned on yet.
    """
    all_ = available_payment_methods()
    enabled = [(v, l) for (v, l, d) in all_ if not d]
    return enabled



def _stripe():
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def create_stripe_payment_intent(order):
    """Create a PaymentIntent for an order. Returns the stripe object."""
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe.PaymentIntent.create(
        amount=int(round(order.total, 2) * 100),
        currency=settings.STRIPE_CURRENCY,
        metadata={'order_number': order.order_number},
        description=f'Newa Store order {order.order_number}',
        receipt_email=order.email or None,
    )


def retrieve_stripe_payment_intent(intent_id):
    """Server-side confirmation that a PaymentIntent succeeded."""
    return _stripe().PaymentIntent.retrieve(intent_id)



def _paypal_token():
    import requests
    resp = requests.post(
        f"{PAYPAL_API_BASE[settings.PAYPAL_SANDBOX]}/v1/oauth2/token",
        auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_SECRET),
        data={'grant_type': 'client_credentials'},
        timeout=20,
    )
    if resp.status_code != 200:
        raise PayPalError(f'PayPal auth failed (HTTP {resp.status_code}).')
    return resp.json()['access_token']


def create_paypal_order(order, return_url, cancel_url):
    """Create a PayPal capture-intent order and return its approval URL."""
    import requests
    token = _paypal_token()
    body = {
        'intent': 'CAPTURE',
        'purchase_units': [{
            'reference_id': order.order_number,
            'description': f'Newa Store order {order.order_number}',
            'amount': {
                'currency_code': settings.PAYPAL_CURRENCY,
                'value': f'{order.total:.2f}',
            },
        }],
        'application_context': {
            'brand_name': 'Newa Store',
            'user_action': 'PAY_NOW',
            'return_url': return_url,
            'cancel_url': cancel_url,
        },
    }
    resp = requests.post(
        f"{PAYPAL_API_BASE[settings.PAYPAL_SANDBOX]}/v2/checkout/orders",
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        json=body,
        timeout=20,
    )
    if resp.status_code not in (200, 201):
        raise PayPalError(f'PayPal order create failed (HTTP {resp.status_code}).')
    data = resp.json()
    for link in data.get('links', []):
        if link.get('rel') == 'approve':
            return link['href']
    raise PayPalError('PayPal did not return an approval link.')


def capture_paypal_order(paypal_order_id):
    """Capture an approved PayPal order. Returns the capture response dict."""
    import requests
    token = _paypal_token()
    resp = requests.post(
        f"{PAYPAL_API_BASE[settings.PAYPAL_SANDBOX]}/v2/checkout/orders/{paypal_order_id}/capture",
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        timeout=20,
    )
    data = resp.json()
    if data.get('status') == 'COMPLETED':
        return data
    raise PayPalError(f'PayPal capture failed: {data.get("message", "unknown error")}')