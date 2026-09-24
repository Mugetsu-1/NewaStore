"""Public JSON API — mounted at /api/.

Endpoints:
    GET  /api/search/?q=            top-20 title matches (trigram-indexed)
    GET  /api/browse/               paginated catalog with filters + sorting
    GET  /api/games/<slug>/         full product detail
    GET  /api/genres/               genres with product counts
    POST /api/contact/              contact form -> ContactMessage + admin email
    GET  /api/health/               DB + cache liveness probe
"""

from django.core.cache import cache
from django.db import connection
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404

from rest_framework import serializers
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from .models import Product, Tag, SiteSettings
from .forms import ContactForm
from .utils import send_templated_email


class APIPagination(PageNumberPagination):
    page_size = 24
    page_size_query_param = 'page_size'
    max_page_size = 100


class ContactRateThrottle(SimpleRateThrottle):
    """Limits POST /api/contact/ to 5/min/IP (rate defined in REST_FRAMEWORK)."""
    scope = 'contact'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class GenreSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    product_count = serializers.IntegerField()


class ProductImageSerializer(serializers.Serializer):
    src = serializers.CharField(source='src_url')
    thumb = serializers.CharField(source='thumbnail_url')
    alt = serializers.CharField(source='alt_text', default='')
    is_primary = serializers.BooleanField(default=False)


class ProductListSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    genres = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'slug', 'name', 'category', 'genres',
            'price', 'discount_price', 'current_price', 'discount_percentage',
            'steam_app_id', 'metacritic_score', 'is_digital',
            'image', 'url',
        ]

    def get_image(self, obj):
        img = obj.images.first()
        return img.thumbnail_url if img else ''

    def get_url(self, obj):
        return obj.get_absolute_url()

    def get_category(self, obj):
        return obj.category.name if obj.category else ''

    def get_genres(self, obj):
        return [t.name for t in obj.tags.all()[:6]]


class ProductDetailSerializer(ProductListSerializer):
    images = serializers.SerializerMethodField()
    reviews = serializers.SerializerMethodField()

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + [
            'description', 'short_description', 'external_url',
            'average_rating', 'review_count', 'published_at', 'images', 'reviews',
        ]

    def get_images(self, obj):
        return ProductImageSerializer(obj.images.all()[:8], many=True).data

    def get_reviews(self, obj):
        reviews = obj.reviews.filter(is_approved=True).select_related('user')[:5]
        return [{
            'user': r.user.username,
            'rating': r.rating,
            'title': r.title,
            'comment': r.comment,
            'created': r.created_at.isoformat(),
        } for r in reviews]


def _filtered_products(params):
    """Shared filter machine used by /api/browse/ (mirrors store.views.product_list)."""
    products = Product.objects.filter(is_active=True).select_related('category') \
        .prefetch_related('images', 'tags')

    category_slug = params.get('category')
    tag_slug = params.get('tag')
    q = params.get('q')

    # Default browse is games-only; an explicit category/tag/query may reach apps.
    if not (category_slug or tag_slug or (q or '').strip()):
        products = products.filter(product_type=Product.PRODUCT_TYPE_GAME)

    if category_slug:
        products = products.filter(category__slug=category_slug)
    if tag_slug:
        products = products.filter(tags__slug=tag_slug)

    if q:
        products = products.filter(
            Q(name__icontains=q) |
            Q(short_description__icontains=q) |
            Q(tags__name__icontains=q)
        ).distinct()

    min_price = params.get('min_price')
    if min_price:
        products = products.filter(price__gte=min_price)
    max_price = params.get('max_price')
    if max_price:
        products = products.filter(price__lte=max_price)

    if params.get('in_stock_only'):
        products = products.filter(stock_quantity__gt=0)
    if params.get('on_sale_only'):
        products = products.filter(discount_price__isnull=False)

    sort_by = params.get('sort_by', '-created_at')
    allowed_sorts = ['-created_at', 'name', '-name', 'price', '-price', '-is_featured', 'newest']
    if sort_by in allowed_sorts:
        products = products.order_by(sort_by)

    return products



@api_view(['GET'])
def search(request):
    q = (request.GET.get('q') or '').strip()
    products = Product.objects.filter(is_active=True).prefetch_related('images', 'tags')
    if q:
        products = products.filter(Q(name__icontains=q) | Q(short_description__icontains=q))[:20]
    else:
        products = products[:20]
    return Response({
        'query': q,
        'count': len(products),
        'results': ProductListSerializer(products, many=True).data,
    })


@api_view(['GET'])
def browse(request):
    products = _filtered_products(request.query_params)
    paginator = APIPagination()
    page = paginator.paginate_queryset(products, request)
    return paginator.get_paginated_response(ProductListSerializer(page, many=True).data)


@api_view(['GET'])
def game_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related('images', 'tags', 'variants'),
        slug=slug, is_active=True,
    )
    return Response(ProductDetailSerializer(product).data)


@api_view(['GET'])
def genres(request):
    qs = (Tag.objects
          .annotate(product_count=Count('products', filter=Q(
              products__is_active=True,
              products__product_type=Product.PRODUCT_TYPE_GAME)))
          .filter(product_count__gt=0)
          .order_by('-product_count')[:50])
    return Response(GenreSerializer(qs, many=True).data)


@api_view(['POST'])
@throttle_classes([ContactRateThrottle])
def contact(request):
    form = ContactForm(request.data or None)
    if not form.is_valid():
        return Response({'success': False, 'errors': dict(form.errors)}, status=400)
    msg = form.save()
    send_templated_email(
        f'New contact message: {msg.subject}',
        'emails/contact_notification.html',
        {'contact': msg},
        [SiteSettings.get_settings().email or 'newastore8@gmail.com'],
    )
    return Response({'success': True, 'message': 'Thank you! We will get back to you soon.'}, status=201)


@api_view(['GET'])
def health(request):
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        db_ok = False

    cache_ok = True
    try:
        cache.set('api-health-probe', 'ok', 5)
        cache_ok = cache.get('api-health-probe') == 'ok'
    except Exception:
        cache_ok = False

    healthy = db_ok and cache_ok
    return Response({
        'status': 'ok' if healthy else 'degraded',
        'database': db_ok,
        'cache': cache_ok,
    }, status=200 if healthy else 503)