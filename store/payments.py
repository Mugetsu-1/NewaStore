"""Payment gateway integration layer (Stripe / PayPal / eSewa / Khalti).

Keeps raw HTTP/SDK calls out of views so the checkout and webhook handlers
stay thin and unit-testable. Every function degrades gracefully when the
gateway is not configured (empty keys in settings).
"""

from django.conf import settings

PAYPAL_API_BASE = {
    True: 'https://api-m.sandbox.paypal.com',
    False: 'https://api-m.paypal.com',
}


class PayPalError(Exception):
    """Raised for any non-success PayPal API response."""


# ---------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------

def is_stripe_configured():
    return bool(settings.STRIPE_PUBLIC_KEY and settings.STRIPE_SECRET_KEY)


def is_paypal_configured():
    return bool(settings.PAYPAL_CLIENT_ID and settings.PAYPAL_SECRET)


def available_payment_methods():
    """Checkout radio choices, marking unconfigured gateways as disabled hints.

    Each entry is a (value, label, disabled) tuple so the template can render
    unavailable methods as disabled placeholders instead of silently dropping
    them (keeps the checkout UI looking complete before keys are added).
    """
    methods = [
        ('esewa', 'eSewa', False),
        ('khalti', 'Khalti', False),
        ('stripe', 'Credit/Debit Card (Stripe)', False),
        ('paypal', 'PayPal', False),
        ('cod', 'Cash on Delivery', False),
        ('bank_transfer', 'Bank Transfer', False),
    ]
    if not is_stripe_configured():
        methods = [m if m[0] != 'stripe' else (m[0], m[1], True) for m in methods]
    if not is_paypal_configured():
        methods = [m if m[0] != 'paypal' else (m[0], m[1], True) for m in methods]
    return methods


def _available_payment_method_choices():
    """Form-compatible version: only selectable (enabled) gateways become choices.

    Disabled/unconfigured gateways still mention in the field's help text so the
    user sees they exist but are not turned on yet.
    """
    all_ = available_payment_methods()
    enabled = [(v, l) for (v, l, d) in all_ if not d]
    return enabled


# ---------------------------------------------------------------
# Stripe (official stripe-python SDK)
# ---------------------------------------------------------------

def _stripe():
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def create_stripe_payment_intent(order):
    """Create a PaymentIntent for an order. Returns the stripe object."""
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe.PaymentIntent.create(
        amount=int(round(order.total, 2) * 100),  # minor units
        currency=settings.STRIPE_CURRENCY,
        metadata={'order_number': order.order_number},
        description=f'Newa Store order {order.order_number}',
        receipt_email=order.email or None,
    )


def retrieve_stripe_payment_intent(intent_id):
    """Server-side confirmation that a PaymentIntent succeeded."""
    return _stripe().PaymentIntent.retrieve(intent_id)


# ---------------------------------------------------------------
# PayPal (Orders v2 REST API via requests — no SDK dependency)
# ---------------------------------------------------------------

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