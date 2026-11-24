import logging
import os
import uuid
from decimal import Decimal
from io import BytesIO

logger = logging.getLogger(__name__)

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from .security import get_client_ip as _security_client_ip


def absolute_site_url(path):
    base = getattr(settings, 'SITE_BASE_URL', 'http://localhost:8000').rstrip('/')
    return f"{base}{path}"


def generate_order_number():
    return f"ORD-{uuid.uuid4().hex.upper()}"


def calculate_tax(subtotal, rate=None):
    from .models import SiteSettings
    if rate is None:
        rate = SiteSettings.get_settings().tax_rate
    return (Decimal(str(subtotal)) * Decimal(str(rate)) / Decimal('100')).quantize(Decimal('0.01'))


def get_client_ip(request):
    return _security_client_ip(request)


def send_templated_email(subject, template_name, context, to_emails, from_email=None):
    if isinstance(to_emails, str):
        to_emails = [to_emails]
    to_emails = [address for address in to_emails if address]
    if not to_emails:
        return 0
    subject = ' '.join(str(subject).splitlines())[:998]
    from_email = from_email or getattr(settings, 'DEFAULT_FROM_EMAIL', 'newastore8@gmail.com')
    html_content = render_to_string(template_name, context)
    text_content = strip_tags(html_content)
    msg = EmailMultiAlternatives(subject, text_content, from_email, to_emails)
    msg.attach_alternative(html_content, "text/html")
    try:
        msg.send(fail_silently=False)
    except Exception:
        logger.exception('Email send failed (subject=%r, recipient_count=%d)', subject, len(to_emails))
        return 0
    return 1


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
    order_url = (
        absolute_site_url(reverse('order_detail', args=[order.order_number]))
        if order.user_id else ''
    )
    context = {'order': order, 'order_url': order_url}
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
        'home_url': absolute_site_url(reverse('home')),
    }
    send_templated_email(
        'Welcome to NewaStore!',
        'emails/welcome.html',
        context,
        [user.email],
    )


def send_email_verification(user):
    """Send a canonical, signed one-time account activation link."""
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    verify_url = absolute_site_url(
        reverse('verify_email', kwargs={'uidb64': uid, 'token': token})
    )
    return send_templated_email(
        'Verify your NewaStore email address',
        'emails/verify_email.html',
        {'user': user, 'verify_url': verify_url},
        [user.email],
    )


def _send_payment_emails(order_id):
    from .models import Order
    order = Order.objects.prefetch_related('items').get(pk=order_id)
    send_order_status_update(order)
    send_payment_receipt(order)


def mark_order_paid(order, gateway='', txn_id=''):
    """Idempotent payment fulfillment shared by all gateways.

    Flips payment_status -> paid, status -> confirmed, stamps the
    transaction id and sends the status + receipt emails. Safe to call
    more than once (e.g. a client return racing a webhook).
    """
    from django.db import transaction
    from django.utils import timezone
    from .models import Order

    order_id = order.pk if hasattr(order, 'pk') else order

    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.payment_status == 'paid':
            return order
        if order.inventory_released or order.status in {'cancelled', 'refunded'}:
            raise ValidationError(
                'This order was cancelled or refunded and cannot be fulfilled automatically.'
            )
        if txn_id:
            order.payment_transaction_id = str(txn_id)[:100]
        if gateway:
            order.payment_method = gateway
        order.payment_status = 'paid'
        if order.status != 'confirmed':
            order.status = 'confirmed'
            order.confirmed_at = timezone.now()
        order.save()
        transaction.on_commit(lambda oid=order.pk: _send_payment_emails(oid))
    return order


def release_order_inventory(order):
    """Idempotently restore reserved stock and coupon usage for a cancellation."""
    from django.db import transaction
    from django.db.models import F
    from .models import Coupon, CouponUsage, Order, Product, ProductVariant

    order_id = order.pk if hasattr(order, 'pk') else order
    with transaction.atomic():
        locked = Order.objects.select_for_update().prefetch_related('items').get(pk=order_id)
        if locked.inventory_released:
            return locked
        items = list(locked.items.all())
        product_ids = sorted({item.product_id for item in items if item.product_id})
        variant_ids = sorted({item.variant_id for item in items if item.variant_id})
        products = {
            product.pk: product
            for product in Product.objects.select_for_update()
            .filter(pk__in=product_ids).order_by('pk')
        }
        # Lock variants in a deterministic order before any stock update.
        list(
            ProductVariant.objects.select_for_update()
            .filter(pk__in=variant_ids).order_by('pk')
        )
        for item in items:
            if not item.product_id:
                continue
            product = products.get(item.product_id)
            if not product or not product.track_inventory:
                continue
            try:
                reserved = int((item.product_snapshot or {}).get(
                    'inventory_reserved', item.quantity
                ))
            except (TypeError, ValueError):
                reserved = item.quantity
            reserved = max(0, min(reserved, item.quantity))
            if not reserved:
                continue
            if item.variant_id:
                ProductVariant.objects.filter(pk=item.variant_id).update(
                    stock_quantity=F('stock_quantity') + reserved
                )
            else:
                Product.objects.filter(pk=item.product_id).update(
                    stock_quantity=F('stock_quantity') + reserved
                )
        if locked.coupon_id:
            Coupon.objects.filter(pk=locked.coupon_id, used_count__gt=0).update(
                used_count=F('used_count') - 1
            )
            CouponUsage.objects.filter(order=locked).delete()
        locked.inventory_released = True
        locked.save(update_fields=['inventory_released', 'updated_at'])
        return locked


def expire_order_reservation(order, now=None):
    """Cancel one overdue unpaid reservation and release its resources."""
    from django.db import transaction
    from .models import Order, OrderStatusHistory

    order_id = order.pk if hasattr(order, 'pk') else order
    now = now or timezone.now()
    with transaction.atomic():
        locked = Order.objects.select_for_update().get(pk=order_id)
        if (
            locked.is_paid or locked.inventory_released
            or locked.status in {'cancelled', 'completed', 'refunded'}
            or not locked.payment_expires_at
            or locked.payment_expires_at > now
        ):
            return False
        previous = locked.status
        release_order_inventory(locked)
        Order.objects.filter(pk=locked.pk).update(
            status='cancelled', updated_at=now
        )
        OrderStatusHistory.objects.create(
            order=locked, status='cancelled',
            notes=f'Unpaid reservation expired (previous status: {previous})',
        )
        return True


def expire_stale_orders(limit=25):
    """Bounded lazy sweep used by checkout in addition to the management command."""
    from .models import Order

    now = timezone.now()
    ids = list(
        Order.objects.filter(
            payment_expires_at__lte=now,
            payment_status__in=['pending', 'failed'],
            inventory_released=False,
        ).exclude(status__in=['cancelled', 'completed', 'refunded'])
        .order_by('payment_expires_at')
        .values_list('pk', flat=True)[:max(1, min(int(limit), 100))]
    )
    expired = 0
    for order_id in ids:
        expired += int(expire_order_reservation(order_id, now=now))
    return expired


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
