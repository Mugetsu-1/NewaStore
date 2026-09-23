import logging
import os
import uuid
from decimal import Decimal
from io import BytesIO

logger = logging.getLogger(__name__)

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags


def _absolute(path):
    base = getattr(settings, 'SITE_BASE_URL', 'http://localhost:8000').rstrip('/')
    return f"{base}{path}"


def generate_order_number():
    return f"ORD-{uuid.uuid4().hex[:10].upper()}"


def calculate_tax(subtotal, rate=None):
    from .models import SiteSettings
    if rate is None:
        rate = SiteSettings.get_settings().tax_rate
    return (Decimal(str(subtotal)) * Decimal(str(rate)) / Decimal('100')).quantize(Decimal('0.01'))


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def send_templated_email(subject, template_name, context, to_emails, from_email=None):
    if isinstance(to_emails, str):
        to_emails = [to_emails]
    from_email = from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'newastore8@gmail.com')
    html_content = render_to_string(template_name, context)
    text_content = strip_tags(html_content)
    msg = EmailMultiAlternatives(subject, text_content, from_email, to_emails)
    msg.attach_alternative(html_content, "text/html")
    try:
        msg.send(fail_silently=False)
    except Exception:
        # Never break the storefront on SMTP hiccups, but DO make failures
        # visible (bad app password, rate limit, ...) in logs/terminal.
        logger.exception('Email send failed (subject=%r, to=%s)', subject, to_emails)


def send_order_confirmation(order):
    context = {'order': order}
    send_templated_email(
        f"Order Confirmation - {order.order_number}",
        'emails/order_confirmation.html',
        context,
        [order.email],
    )


def send_order_status_update(order):
    context = {'order': order}
    send_templated_email(
        f"Your order {order.order_number} is now {order.get_status_display()}",
        'emails/order_status_update.html',
        context,
        [order.email],
    )


def send_payment_receipt(order):
    """Receipt sent the moment a payment is captured by any gateway."""
    context = {'order': order, 'order_url': _absolute(reverse('order_detail', args=[order.order_number]))}
    send_templated_email(
        f"Payment received — {order.order_number}",
        'emails/payment_receipt.html',
        context,
        [order.email],
    )


def send_welcome_email(user):
    from .models import Product
    context = {
        'user': user,
        'total_products': Product.objects.filter(is_active=True).count(),
        'home_url': _absolute(reverse('home')),
    }
    send_templated_email(
        'Welcome to Newa Store!',
        'emails/welcome.html',
        context,
        [user.email],
    )


def mark_order_paid(order, gateway='', txn_id=''):
    """Idempotent payment fulfillment shared by all gateways.

    Flips payment_status -> paid, status -> confirmed, stamps the
    transaction id and sends the status + receipt emails. Safe to call
    more than once (e.g. a client return racing a webhook).
    """
    from django.db import transaction
    from django.utils import timezone

    if order.payment_status == 'paid':
        return order

    with transaction.atomic():
        if txn_id:
            order.payment_transaction_id = txn_id
        if gateway:
            order.payment_method = gateway
        if order.payment_status != 'paid':
            order.payment_status = 'paid'
        if order.status != 'confirmed':
            order.status = 'confirmed'
            order.confirmed_at = timezone.now()
        order.save()  # fires the OrderStatusHistory pre_save signal
        send_order_status_update(order)
        send_payment_receipt(order)
    order.refresh_from_db()
    return order


def build_invoice_pdf(order):
    """Render a simple PDF invoice. Uses reportlab if available, else HTML bytes."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas

        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        width, height = A4

        p.setFont("Helvetica-Bold", 18)
        p.drawString(20 * mm, height - 25 * mm, "INVOICE")
        p.setFont("Helvetica", 10)
        p.drawString(20 * mm, height - 32 * mm, f"Order: {order.order_number}")
        p.drawString(20 * mm, height - 38 * mm, f"Date: {order.created_at.strftime('%Y-%m-%d')}")
        p.drawString(20 * mm, height - 44 * mm, f"Status: {order.get_status_display()}")

        addr = order.billing_address or {}
        p.drawString(120 * mm, height - 32 * mm, "Bill To:")
        p.drawString(120 * mm, height - 38 * mm, str(addr.get('full_name', '')))
        p.drawString(120 * mm, height - 44 * mm, str(addr.get('address_line_1', '')))
        p.drawString(120 * mm, height - 50 * mm, f"{addr.get('city', '')}, {addr.get('country', '')}")

        y = height - 65 * mm
        p.setFont("Helvetica-Bold", 11)
        p.drawString(20 * mm, y, "Item")
        p.drawString(110 * mm, y, "Qty")
        p.drawString(130 * mm, y, "Unit")
        p.drawString(160 * mm, y, "Total")
        p.setFont("Helvetica", 10)
        y -= 7 * mm
        for item in order.items.all():
            p.drawString(20 * mm, y, item.product_name[:50])
            p.drawString(110 * mm, y, str(item.quantity))
            p.drawString(130 * mm, y, f"{item.unit_price}")
            p.drawString(160 * mm, y, f"{item.total_price}")
            y -= 6 * mm
            if y < 40 * mm:
                p.showPage()
                y = height - 25 * mm

        y -= 5 * mm
        p.setFont("Helvetica-Bold", 11)
        p.drawString(130 * mm, y, f"Subtotal: {order.subtotal}")
        y -= 6 * mm
        p.drawString(130 * mm, y, f"Discount: -{order.discount_amount}")
        y -= 6 * mm
        p.drawString(130 * mm, y, f"Tax: {order.tax_amount}")
        y -= 8 * mm
        p.setFont("Helvetica-Bold", 13)
        p.drawString(130 * mm, y, f"TOTAL: {order.total}")

        p.showPage()
        p.save()
        buffer.seek(0)
        return buffer.getvalue()
    except ImportError:
        return None


def low_stock_products():
    from django.db.models import F
    from .models import Product
    return Product.objects.filter(
        track_inventory=True, is_active=True,
        stock_quantity__lte=F('low_stock_threshold')
    )


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
