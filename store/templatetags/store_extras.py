from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()


@register.filter
def stars(rating):
    """Render star rating as HTML."""
    try:
        rating = float(rating)
    except (TypeError, ValueError):
        rating = 0
    rating = min(5, max(0, rating))
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    icons = [('fas fa-star',)] * full
    if half:
        icons.append(('fas fa-star-half-alt',))
    icons.extend([('far fa-star',)] * empty)
    rendered = format_html_join('', '<i class="{}"></i>', icons)
    return format_html('<span class="stars">{}</span>', rendered)


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
    return format_html(
        '<span class="badge badge-{}">{}</span>', color, str(status).title()
    )


@register.filter
def in_wishlist(product, wishlist):
    if not wishlist:
        return False
    return wishlist.items.filter(product=product).exists()
