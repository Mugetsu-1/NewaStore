from django.core.cache import cache
from django.db.models import Count, Q

from .models import SiteSettings, Category, Tag, Cart
from .cart import CartManager

GENRE_CACHE_KEY = 'nav_genres_v1'
GENRE_CACHE_TTL = 60


def site_settings(request):
    try:
        settings_obj = SiteSettings.get_settings()
    except Exception:
        settings_obj = None
    return {
        'site_settings': settings_obj,
        'currency_symbol': settings_obj.currency_symbol if settings_obj else 'Rs.',
    }


def navigation(request):
    categories = Category.objects.filter(is_active=True, parent__isnull=True).prefetch_related('children')

    genres = cache.get(GENRE_CACHE_KEY)
    if genres is None:
        genres = list(
            Tag.objects
            .annotate(product_count=Count('products', filter=Q(products__is_active=True)))
            .filter(product_count__gt=0)
            .order_by('-product_count')[:10]
        )
        cache.set(GENRE_CACHE_KEY, genres, GENRE_CACHE_TTL)

    return {'nav_categories': categories, 'nav_genres': genres}


def cart_context(request):
    try:
        cart = CartManager(request).cart
    except Exception:
        cart = None
    return {'cart': cart}
