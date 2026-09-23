from django import template
from django.utils.safestring import mark_safe

register = template.Library()


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
def in_wishlist(product, wishlist):
    if not wishlist:
        return False
    return wishlist.items.filter(product=product).exists()
