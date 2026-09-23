from decimal import Decimal, InvalidOperation

from django import template
from django.utils.safestring import mark_safe
from django.utils.html import escape

register = template.Library()


@register.filter
def currency(value, symbol='Rs.'):
    try:
        value = Decimal(str(value))
        return f"{symbol} {value:,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return f"{symbol} 0.00"


@register.filter
def percent(value):
    try:
        return f"{int(value)}%"
    except (TypeError, ValueError):
        return "0%"


@register.filter
def multiply(value, arg):
    try:
        return Decimal(str(value)) * Decimal(str(arg))
    except (InvalidOperation, TypeError, ValueError):
        return 0


@register.filter
def divide(value, arg):
    try:
        if Decimal(str(arg)) == 0:
            return 0
        return Decimal(str(value)) / Decimal(str(arg))
    except (InvalidOperation, TypeError, ValueError):
        return 0


@register.filter
def stars(rating):
    """Render star rating as HTML."""
    try:
        rating = float(rating)
    except (TypeError, ValueError):
        rating = 0
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    html = '<span class="stars">'
    html += '<i class="fas fa-star"></i>' * full
    if half:
        html += '<i class="fas fa-star-half-alt"></i>'
    html += '<i class="far fa-star"></i>' * empty
    html += '</span>'
    return mark_safe(html)


@register.filter
def product_image(product):
    """Return the best available image URL for a product."""
    primary = product.images.filter(is_primary=True).first() or product.images.first()
    if primary and primary.image:
        return primary.image.url
    return ''


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs):
    query = context['request'].GET.copy()
    for key, value in kwargs.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    if query:
        return '?' + query.urlencode()
    return ''


@register.simple_tag
def order_status_badge(status):
    colors = {
        'pending': 'warning',
        'confirmed': 'info',
        'processing': 'info',
        'completed': 'success',
        'cancelled': 'danger',
        'refunded': 'secondary',
    }
    color = colors.get(status, 'secondary')
    return mark_safe(f'<span class="badge badge-{color}">{status.title()}</span>')


@register.filter
def get_item(mapping, key):
    if isinstance(mapping, dict):
        return mapping.get(key)
    return None


@register.filter
def in_wishlist(product, wishlist):
    if not wishlist:
        return False
    return wishlist.items.filter(product=product).exists()
