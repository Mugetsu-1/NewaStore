import base64
import binascii
import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth.views import (
    LoginView, PasswordResetView, PasswordResetDoneView,
    PasswordResetConfirmView, PasswordResetCompleteView,
)
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q, Count, F
from django.http import JsonResponse, HttpResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.decorators import method_decorator
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_GET, require_http_methods

from .payments import (
    is_esewa_configured, build_esewa_form, verify_esewa_signature,
    verify_esewa_status, BANK_TRANSFER_DETAILS,
)
from .models import (
    Product, Category, Tag, ProductVariant, ProductImage, Review,
    Coupon, CouponUsage, Wishlist, WishlistItem,
    Order, OrderItem, OrderStatusHistory,
    NewsletterSubscriber, ContactMessage, SiteSettings, CartItem, Cart,
    SavedBillingDetail, ReviewHelpfulVote,
)
from .forms import (
    CustomRegisterForm, UserProfileForm, ReviewForm,
    CheckoutForm, CouponApplyForm, NewsletterForm, ContactForm,
    ProductSearchForm,
)
from .cart import CartManager
from .recommendations import recommend_for_product
from .utils import (
    calculate_tax, send_order_confirmation,
    build_invoice_pdf, get_client_ip, send_templated_email,
    send_welcome_email, send_email_verification, mark_order_paid, release_order_inventory,
    expire_order_reservation, expire_stale_orders, absolute_site_url,
)
from .security import (
    consume_rate_limit, rate_limit, safe_redirect_target,
)


GUEST_ORDER_SESSION_KEY = 'guest_order_numbers'
MAX_PAYMENT_CALLBACK_BYTES = 16 * 1024
logger = logging.getLogger(__name__)


def _remember_guest_order(request, order):
    if order.user_id:
        return
    numbers = list(request.session.get(GUEST_ORDER_SESSION_KEY, []))
    if order.order_number not in numbers:
        numbers.append(order.order_number)
    request.session[GUEST_ORDER_SESSION_KEY] = numbers[-20:]
    request.session.modified = True


def _can_access_order(request, order):
    if request.user.is_authenticated:
        if request.user.is_staff:
            return True
        if order.user_id:
            return order.user_id == request.user.id
    if order.user_id:
        return False
    return order.order_number in request.session.get(GUEST_ORDER_SESSION_KEY, [])


def _owned_order_or_404(request, order_number, queryset=None):
    queryset = queryset if queryset is not None else Order.objects.all()
    order = get_object_or_404(queryset, order_number=order_number)
    if not _can_access_order(request, order):
        raise Http404
    return order


def _decimal_filter(value):
    if value in (None, ''):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0 or parsed > Decimal('99999999.99'):
        return None
    return parsed


def home(request):
    products = Product.games.filter(is_active=True).select_related('category')\
        .prefetch_related('images', 'tags')
    featured = products.filter(is_featured=True)[:8]

    hero_qs = (products.filter(is_featured=True)
               .order_by(F('discount_price').desc(nulls_last=True))[:4])
    hero_slides = []
    for p in hero_qs:
        imgs = list(p.images.all())
        if not imgs:
            continue
        bg = None
        for img in imgs:
            if (img.sort_order or 0) > 0:
                bg = img
        hero_slides.append({
            'product': p,
            'bg_url': (bg or imgs[0]).thumbnail_url,
            'art_url': imgs[0].thumbnail_url,
        })

    new_arrivals = products.order_by(F('published_at').desc(nulls_last=True))[:8]
    deals = products.filter(discount_price__isnull=False).order_by('-discount_price')[:8]
    categories = Category.objects.filter(is_active=True, parent__isnull=True).annotate(
        num_products=Count('products', filter=Q(products__is_active=True))
    )[:9]
    best_sellers = products.annotate(order_count=Count('order_items'))\
        .filter(order_count__gt=0).order_by('-order_count')[:8]
    genres = Tag.objects.annotate(
        product_count=Count('products', filter=Q(
            products__is_active=True,
            products__product_type=Product.PRODUCT_TYPE_GAME))
    ).filter(product_count__gt=0).order_by('-product_count')[:12]

    hero_covers = [
        img.card_image_url
        for img in (ProductImage.objects.filter(
                        product__is_active=True,
                        product__product_type=Product.PRODUCT_TYPE_GAME)
                    .exclude(thumbnail='').only('thumbnail', 'image')
                    .order_by('-id')[:24])
        if img.card_image_url
    ]
    hero_collage = [hero_covers[i::3] for i in range(3)] if len(hero_covers) >= 6 else []
    hero_stats = {
        'games': f"{products.count():,}",
        'deals': f"{products.filter(discount_price__isnull=False).count():,}",
        'genres': f"{Tag.objects.filter(products__is_active=True, products__product_type=Product.PRODUCT_TYPE_GAME).distinct().count():,}",
    }

    context = {
        'featured_products': featured,
        'hero_slides': hero_slides,
        'hero_collage': hero_collage,
        'hero_stats': hero_stats,
        'new_arrivals': new_arrivals,
        'deal_products': deals,
        'categories': categories,
        'best_sellers': best_sellers,
        'genres': genres,
        'page_title': 'Home',
    }
    return render(request, 'store/home.html', context)


@rate_limit('catalog-browse', limit=120, window=60, methods=('GET',))
def product_list(request):
    products = Product.objects.filter(is_active=True).select_related('category')\
        .prefetch_related('images', 'tags')
    form = ProductSearchForm(request.GET)

    # The default shop listing showcases games; an explicit category (e.g.
    # Applications), tag or search query may surface non-game products.
    has_scope = bool(request.GET.get('category') or request.GET.get('tag')
                     or (request.GET.get('q') or '').strip())
    if not has_scope:
        products = products.filter(product_type=Product.PRODUCT_TYPE_GAME)

    category_slug = request.GET.get('category')
    if category_slug:
        products = products.filter(category__slug=category_slug)

    tag_slug = request.GET.get('tag')
    if tag_slug:
        products = products.filter(tags__slug=tag_slug)

    q = (request.GET.get('q') or '').strip()[:200]
    if q:
        products = products.filter(
            Q(name__icontains=q) |
            Q(short_description__icontains=q) |
            Q(tags__name__icontains=q) |
            Q(category__name__icontains=q)
        ).distinct()

        if settings.ENABLE_LIVE_SEARCH_IMPORT and len(q) >= 3 and not products.exists():
            allowed_import, _ = consume_rate_limit(
                request, 'catalog-live-import', 6, 3600
            )
        else:
            allowed_import = False
        if allowed_import:
            try:
                from .importers import import_search_query
                if import_search_query(q) > 0:
                    products = products.filter(
                        Q(name__icontains=q) |
                        Q(short_description__icontains=q) |
                        Q(tags__name__icontains=q) |
                        Q(category__name__icontains=q)
                    ).distinct()
                    messages.info(request,
                                  f'Imported live results for "{q}" from the '
                                  f'international stores.')
            except Exception:
                logger.exception('Live catalog import failed')

    min_price = _decimal_filter(request.GET.get('min_price'))
    max_price = _decimal_filter(request.GET.get('max_price'))
    if min_price is not None:
        products = products.filter(price__gte=min_price)
    if max_price is not None:
        products = products.filter(price__lte=max_price)

    if request.GET.get('in_stock_only'):
        products = products.filter(stock_quantity__gt=0)
    if request.GET.get('on_sale_only'):
        products = products.filter(discount_price__isnull=False)

    sort_by = request.GET.get('sort_by') or 'featured'
    sort_map = {
        'featured': ('-is_featured', '-tier', '-owners', '-created_at'),
        '-created_at': ('-created_at',),
        'newest': ('-tier', F('published_at').desc(nulls_last=True)),
        'name': ('name',),
        '-name': ('-name',),
        'price': ('price',),
        '-price': ('-price',),
        '-is_featured': ('-is_featured', '-tier', '-owners', '-created_at'),
    }
    products = products.order_by(*sort_map.get(sort_by, sort_map['featured']))

    paginator = Paginator(products, 24)
    page_obj = paginator.get_page(request.GET.get('page'))

    genres = Tag.objects.annotate(
        product_count=Count('products', filter=Q(
            products__is_active=True,
            products__product_type=Product.PRODUCT_TYPE_GAME))
    ).filter(product_count__gt=0).order_by('name')

    context = {
        'page_obj': page_obj,
        'products': page_obj.object_list,
        'form': form,
        'total_count': page_obj.paginator.count,
        'genres': genres,
        'page_title': 'Shop',
    }
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'store/partials/product_grid_fragment.html', context)
    return render(request, 'store/product_list.html', context)


def category_detail(request, slug):
    category = get_object_or_404(Category, slug=slug, is_active=True)
    products = Product.objects.filter(is_active=True, category=category).select_related('category').prefetch_related('images')

    min_price = _decimal_filter(request.GET.get('min_price'))
    max_price = _decimal_filter(request.GET.get('max_price'))
    if min_price is not None:
        products = products.filter(price__gte=min_price)
    if max_price is not None:
        products = products.filter(price__lte=max_price)

    sort_by = request.GET.get('sort_by') or 'featured'
    cat_sorts = {
        'featured': ('-is_featured', '-tier', '-owners', '-created_at'),
        '-created_at': ('-created_at',),
        'name': ('name',),
        '-name': ('-name',),
        'price': ('price',),
        '-price': ('-price',),
    }
    products = products.order_by(*cat_sorts.get(sort_by, cat_sorts['featured']))

    paginator = Paginator(products, 12)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'category': category,
        'page_obj': page_obj,
        'products': page_obj.object_list,
        'subcategories': category.children.filter(is_active=True),
        'page_title': category.name,
    }
    return render(request, 'store/category_detail.html', context)


def tag_detail(request, slug):
    tag = get_object_or_404(Tag, slug=slug)
    products = Product.objects.filter(is_active=True, tags=tag).prefetch_related('images')
    paginator = Paginator(products, 12)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'store/product_list.html', {
        'page_obj': page_obj,
        'products': page_obj.object_list,
        'page_title': f'Tag: {tag.name}',
        'form': ProductSearchForm(),
        'total_count': page_obj.paginator.count,
    })


def search(request):
    return product_list(request)


@rate_limit('search-suggest', limit=120, window=60, methods=('GET',))
def search_suggest(request):
    """Lightweight JSON endpoint for the header search dropdown."""
    q = (request.GET.get('q') or '').strip()[:100]
    if len(q) < 2:
        return JsonResponse({'results': []})
    products = (Product.objects
                .filter(is_active=True, name__icontains=q)
                .prefetch_related('images')[:5])
    results = []
    for p in products:
        img = p.images.first()
        results.append({
            'name': p.name,
            'url': p.get_absolute_url(),
            'image': img.thumbnail_url if img else '',
            'price': str(p.current_price),
        })
    return JsonResponse({'results': results})


def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related('images', 'variants', 'tags'),
        slug=slug, is_active=True
    )
    images = product.images.all()
    variants = product.variants.filter(is_active=True)
    if product.track_inventory and not product.allow_backorder:
        variants = variants.filter(stock_quantity__gt=0)
    reviews = product.reviews.filter(is_approved=True).select_related('user')

    related = recommend_for_product(product, limit=4)

    user_review = None
    in_wishlist = False
    if request.user.is_authenticated:
        user_review = reviews.filter(user=request.user).first()
        wishlist = Wishlist.objects.filter(user=request.user).first()
        if wishlist:
            in_wishlist = wishlist.items.filter(product=product).exists()

    form = ReviewForm(user=request.user if request.user.is_authenticated else None, product=product)

    context = {
        'product': product,
        'images': images,
        'variants': variants,
        'reviews': reviews,
        'related_products': related,
        'user_review': user_review,
        'in_wishlist': in_wishlist,
        'review_form': form,
        'page_title': product.name,
        'meta_title': product.meta_title or product.name,
        'meta_description': product.meta_description or product.short_description or product.description[:160],
    }
    return render(request, 'store/product_detail.html', context)


def quick_view(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    return render(request, 'store/partials/quick_view.html', {'product': product})



def cart_view(request):
    cart = CartManager(request).cart
    context = {
        'cart': cart,
        'items': cart.items.select_related('product', 'variant').all(),
        'page_title': 'Shopping Cart',
    }
    return render(request, 'store/cart.html', context)


@require_POST
@rate_limit('cart-mutation', limit=120, window=60)
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_active=True)

    if not product.is_in_stock:
        messages.error(request, f'"{product.name}" is out of stock.')
        return redirect(safe_redirect_target(request, reverse('home')))

    variant = None
    variant_id = request.POST.get('variant_id')
    if variant_id:
        variant = ProductVariant.objects.filter(id=variant_id, product=product, is_active=True).first()
        if variant is None:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'Invalid product option.'}, status=400)
            messages.error(request, 'The selected product option is not available.')
            return redirect(safe_redirect_target(request, reverse('product_detail', args=[product.slug])))

    try:
        quantity = int(request.POST.get('quantity', 1))
    except (TypeError, ValueError):
        quantity = 0

    manager = CartManager(request)
    try:
        manager.add(product, quantity=quantity, variant=variant)
    except ValidationError as exc:
        message = exc.messages[0]
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': message}, status=400)
        messages.error(request, message)
        return redirect(safe_redirect_target(request, reverse('cart')))

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'cart_count': manager.count, 'message': f'"{product.name}" added to cart.'})

    messages.success(request, f'"{product.name}" added to your cart.')
    return redirect(safe_redirect_target(request, reverse('cart')))


@require_POST
@rate_limit('cart-mutation', limit=120, window=60)
def update_cart(request, item_id):
    manager = CartManager(request)
    try:
        quantity = int(request.POST.get('quantity', 1))
    except (TypeError, ValueError):
        quantity = 1
    try:
        manager.update(item_id, quantity)
    except ValidationError as exc:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': exc.messages[0]}, status=400)
        messages.error(request, exc.messages[0])
        return redirect('cart')

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        cart = manager.cart
        return JsonResponse({
            'success': True,
            'cart_count': manager.count,
            'subtotal': str(cart.subtotal),
            'discount': str(cart.discount_amount),
            'total': str(cart.total),
        })
    return redirect('cart')


@require_POST
def remove_from_cart(request, item_id):
    manager = CartManager(request)
    manager.remove(item_id)
    messages.success(request, 'Item removed from cart.')
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'cart_count': manager.count})
    return redirect('cart')


@require_POST
def clear_cart(request):
    CartManager(request).clear()
    messages.success(request, 'Cart cleared.')
    return redirect('cart')


@require_POST
@rate_limit('coupon-attempt', limit=20, window=600)
def apply_coupon(request):
    manager = CartManager(request)
    form = CouponApplyForm(request.POST)
    if form.is_valid():
        code = form.cleaned_data['code'].strip().upper()
        coupon = Coupon.objects.filter(code__iexact=code).first()
        if not coupon:
            messages.error(request, 'Invalid coupon code.')
        else:
            items = list(manager.items)
            valid, msg = coupon.is_valid(
                request.user if request.user.is_authenticated else None,
                manager.subtotal,
                items,
            )
            if valid:
                manager.apply_coupon(coupon)
                messages.success(request, f'Coupon "{coupon.code}" applied successfully.')
            else:
                messages.error(request, msg)
    return redirect('cart')


@require_POST
def remove_coupon(request):
    CartManager(request).remove_coupon()
    messages.success(request, 'Coupon removed.')
    return redirect('cart')


def cart_mini(request):
    cart = CartManager(request).cart
    return render(request, 'store/partials/cart_mini.html', {'cart': cart})



@rate_limit('checkout', limit=30, window=600)
@sensitive_post_parameters(
    'billing_full_name', 'billing_phone', 'billing_email',
    'billing_address_line_1', 'billing_address_line_2',
)
@never_cache
def checkout(request):
    expire_stale_orders(limit=25)
    manager = CartManager(request)
    if manager.is_empty:
        messages.warning(request, 'Your cart is empty.')
        return redirect('cart')

    site = SiteSettings.get_settings()
    if not request.user.is_authenticated:
        has_digital = manager.items.filter(product__is_digital=True).exists()
        if not site.allow_guest_checkout or (site.require_account_for_digital and has_digital):
            messages.info(request, 'Please sign in before checking out these items.')
            return redirect(f"{reverse('login')}?next={reverse('checkout')}")

    if request.method == 'POST':
        form = CheckoutForm(request.POST, user=request.user, cart=manager.cart)
        if form.is_valid():
            try:
                return _create_order_and_pay(request, form, manager)
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
    else:
        form = CheckoutForm(user=request.user, cart=manager.cart)

    context = {
        'form': form,
        'cart': manager.cart,
        'items': manager.items,
        'subtotal': manager.subtotal,
        'discount': manager.discount,
        'tax': calculate_tax(manager.subtotal - manager.discount),
        'total': (manager.subtotal - manager.discount) + calculate_tax(manager.subtotal - manager.discount),
        'page_title': 'Checkout',
    }
    return render(request, 'store/checkout.html', context)


def _create_order_and_pay(request, form, manager):
    data = form.cleaned_data
    user = request.user if request.user.is_authenticated else None

    billing = {
        'full_name': data['billing_full_name'],
        'phone': data['billing_phone'],
        'email': data['billing_email'],
        'address_line_1': data['billing_address_line_1'],
        'address_line_2': data.get('billing_address_line_2', ''),
        'city': data['billing_city'],
        'state': data['billing_state'],
        'postal_code': data['billing_postal_code'],
        'country': data['billing_country'],
    }
    with transaction.atomic():
        cart = Cart.objects.select_for_update().get(pk=manager.cart.pk)
        items = list(
            CartItem.objects.select_for_update(of=('self',))
            .select_related('product', 'variant')
            .filter(cart=cart)
            .order_by('pk')
        )
        if not items:
            raise ValidationError('Your cart is empty. Please add the items again.')

        product_ids = {item.product_id for item in items}
        products = {
            product.pk: product
            for product in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')
        }
        variant_ids = {item.variant_id for item in items if item.variant_id}
        variants = {
            variant.pk: variant
            for variant in ProductVariant.objects.select_for_update().filter(pk__in=variant_ids).order_by('pk')
        }
        variant_required_products = set(
            ProductVariant.objects.filter(
                product_id__in=product_ids, is_active=True,
            ).values_list('product_id', flat=True)
        )

        subtotal = Decimal('0')
        for item in items:
            product = products.get(item.product_id)
            variant = variants.get(item.variant_id) if item.variant_id else None
            if product is None or not product.is_active:
                raise ValidationError('One of the products in your cart is no longer available.')
            if not 1 <= item.quantity <= 99:
                raise ValidationError('A cart quantity is outside the allowed range.')
            if item.variant_id and (
                variant is None or variant.product_id != product.id or not variant.is_active
            ):
                raise ValidationError('A selected product option is no longer available.')
            if not item.variant_id and product.id in variant_required_products:
                raise ValidationError(f'Choose an option for "{product.name}".')

            item.product = product
            item.variant = variant
            if variant is not None:
                variant.product = product
            if product.track_inventory and not product.allow_backorder:
                available = variant.stock_quantity if variant else product.stock_quantity
                if item.quantity > available:
                    raise ValidationError(
                        f'Only {available} of "{product.name}" remain in stock.'
                    )
            if product.track_inventory:
                available = variant.stock_quantity if variant else product.stock_quantity
                item._inventory_reserved = min(item.quantity, available)
            else:
                item._inventory_reserved = 0
            unit_price = variant.final_price if variant else product.current_price
            if unit_price < 0:
                raise ValidationError('A product has an invalid price.')
            # Pin the server-authoritative price for the immutable order line.
            item._checkout_unit_price = unit_price
            subtotal += unit_price * item.quantity

        coupon = None
        discount = Decimal('0')
        if cart.coupon_id:
            coupon = Coupon.objects.select_for_update().get(pk=cart.coupon_id)
            valid, reason = coupon.is_valid(user, subtotal, items)
            if not valid:
                raise ValidationError(reason)
            discount = coupon.calculate_discount(
                subtotal, coupon.eligible_subtotal(items)
            )
        taxable = max(Decimal('0'), subtotal - discount)
        tax = calculate_tax(taxable)
        total = taxable + tax
        if data['payment_method'] == 'esewa':
            payment_expires_at = timezone.now() + timedelta(
                minutes=settings.ESEWA_ORDER_TTL_MINUTES
            )
        else:
            payment_expires_at = timezone.now() + timedelta(
                hours=settings.BANK_TRANSFER_ORDER_TTL_HOURS
            )

        order = Order.objects.create(
            user=user,
            guest_email=billing['email'] if not user else '',
            guest_phone=billing['phone'] if not user else '',
            billing_address=billing,
            subtotal=subtotal,
            discount_amount=discount,
            tax_amount=tax,
            total=total,
            coupon=coupon,
            payment_method=data['payment_method'],
            notes=data.get('order_notes', ''),
            ip_address=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            payment_expires_at=payment_expires_at,
        )

        for item in items:
            OrderItem.objects.create(
                order=order,
                product=item.product,
                variant=item.variant,
                product_name=item.product.name,
                product_sku=item.product.sku,
                variant_name=item.variant.name if item.variant else '',
                variant_sku=item.variant.sku if item.variant else '',
                unit_price=item._checkout_unit_price,
                quantity=item.quantity,
                product_snapshot={'inventory_reserved': item._inventory_reserved},
            )
            if item.product.track_inventory and item._inventory_reserved:
                if item.variant_id:
                    ProductVariant.objects.filter(pk=item.variant_id).update(
                        stock_quantity=F('stock_quantity') - item._inventory_reserved
                    )
                else:
                    Product.objects.filter(pk=item.product_id).update(
                        stock_quantity=F('stock_quantity') - item._inventory_reserved
                    )

        OrderStatusHistory.objects.create(order=order, status='pending', notes='Order placed')

        if coupon:
            if user:
                CouponUsage.objects.create(
                    coupon=coupon, user=user, order=order,
                    discount_amount=discount,
                )
            Coupon.objects.filter(pk=coupon.pk).update(used_count=F('used_count') + 1)

        if user and data.get('save_details'):
            SavedBillingDetail.objects.update_or_create(
                user=user,
                defaults={
                    'full_name': billing['full_name'],
                    'phone': billing['phone'],
                    'email': billing['email'],
                    'address_line_1': billing['address_line_1'],
                    'address_line_2': billing['address_line_2'],
                    'city': billing['city'],
                    'state': billing['state'],
                    'postal_code': billing['postal_code'],
                    'country': billing['country'],
                },
            )

        cart.items.all().delete()
        cart.coupon = None
        cart.save(update_fields=['coupon', 'updated_at'])
        transaction.on_commit(lambda oid=order.pk: send_order_confirmation(Order.objects.get(pk=oid)))

    _remember_guest_order(request, order)

    payment_method = data['payment_method']
    if payment_method == 'esewa':
        return redirect('esewa_checkout', order_number=order.order_number)
    elif payment_method == 'nay_bank':
        messages.info(
            request,
            'Order placed. Complete the bank transfer using the details below so '
            'we can confirm your order.',
        )
        return redirect('order_success', order_number=order.order_number)
    else:
        return redirect('order_success', order_number=order.order_number)


@rate_limit('esewa-checkout', limit=30, window=600, methods=('GET', 'POST'))
@never_cache
def esewa_checkout(request, order_number):
    """eSewa payment step.

    Against the public sandbox (``settings.ESEWA_SIMULATE``) this renders a
    local gateway page that completes the order in-site — with a clear path
    back to the store — instead of bouncing the shopper off to eSewa's RC host
    with no return. With live credentials it renders the real signed
    auto-submit form and fulfilment still runs through the HMAC-verified
    ``esewa_verify`` callback.
    """
    order = _owned_order_or_404(request, order_number)
    if order.payment_method != 'esewa':
        raise Http404
    if expire_order_reservation(order):
        messages.error(request, 'This unpaid order expired. Please place a new order.')
        return redirect('cart')
    if order.is_paid:
        return redirect('order_success', order_number=order.order_number)
    if not is_esewa_configured():
        messages.error(request, 'eSewa is not available right now. Please choose another method.')
        return redirect('payment_failed', order_number=order.order_number)

    if settings.ESEWA_SIMULATE and settings.DEBUG:
        if request.method == 'POST':
            mark_order_paid(order, gateway='esewa', txn_id=f'SANDBOX-{order.order_number}')
            messages.success(request, 'Payment received via eSewa. Your order is confirmed.')
            return redirect('order_success', order_number=order.order_number)
        return render(request, 'store/esewa_gateway.html', {
            'order': order,
            'page_title': 'eSewa Payment',
        })

    if request.method != 'GET':
        return HttpResponseNotAllowed(['GET'])

    form = build_esewa_form(
        order,
        success_url=absolute_site_url(reverse('esewa_verify')),
        failure_url=absolute_site_url(reverse('payment_failed', args=[order.order_number])),
    )
    return render(request, 'store/esewa_form.html', {
        'order': order,
        'esewa_action': form['action'],
        'esewa_fields': form['fields'],
        'page_title': 'Redirecting to eSewa',
    })


@require_GET
@rate_limit('esewa-callback', limit=60, window=600, methods=('GET',))
@never_cache
def esewa_verify(request):
    """Success callback — trust only eSewa's signed, server-recomputed response.

    eSewa returns the signed fields as base64 JSON in ``?data=``. The order is
    fulfilled only when the HMAC signature verifies, the status is COMPLETE, and
    the returned amount matches the order total to the paisa.
    """
    encoded_data = request.GET.get('data')
    if not encoded_data or len(encoded_data) > MAX_PAYMENT_CALLBACK_BYTES:
        messages.error(request, 'Payment verification failed.')
        return redirect('home')

    try:
        raw = base64.b64decode(encoded_data, validate=True)
        if len(raw) > MAX_PAYMENT_CALLBACK_BYTES:
            raise ValueError('payload too large')
        decoded = raw.decode('utf-8')
        response = json.loads(decoded)
        if not isinstance(response, dict):
            raise ValueError('payload must be an object')
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        messages.error(request, 'Invalid payment response.')
        return redirect('home')

    transaction_uuid = response.get('transaction_uuid')
    if not isinstance(transaction_uuid, str) or len(transaction_uuid) > 40:
        messages.error(request, 'Invalid payment response.')
        return redirect('home')
    order = Order.objects.filter(order_number=transaction_uuid).first()
    if not order:
        messages.error(request, 'Order not found.')
        return redirect('home')

    try:
        paid = Decimal(str(response.get('total_amount', '0')).replace(',', ''))
    except (InvalidOperation, TypeError):
        paid = Decimal('0')

    signature_valid = verify_esewa_signature(response)
    transaction_code = response.get('transaction_code', '')
    verified = (
            signature_valid
            and response.get('status') == 'COMPLETE'
            and response.get('product_code') == settings.ESEWA_PRODUCT_CODE
            and order.payment_method == 'esewa'
            and isinstance(transaction_code, str)
            and bool(transaction_code.strip())
            and paid == order.total.quantize(Decimal('0.01'))
            and verify_esewa_status(order)
    )
    if verified:
        try:
            mark_order_paid(order, gateway='esewa', txn_id=transaction_code)
        except (IntegrityError, ValidationError):
            messages.error(request, 'This payment cannot be applied automatically. Please contact support.')
            return redirect('payment_failed', order_number=order.order_number)
        return redirect('order_success', order_number=order.order_number)

    # Only a cryptographically valid response may alter state. Forged callbacks
    # must not be able to mark an arbitrary pending order as failed.
    if signature_valid and order.payment_status != 'paid':
        Order.objects.filter(pk=order.pk).update(payment_status='failed')
    messages.error(request, 'eSewa payment could not be verified.')
    return redirect('payment_failed', order_number=order.order_number)


@never_cache
def payment_failed(request, order_number):
    order = _owned_order_or_404(request, order_number)
    expire_order_reservation(order)
    order.refresh_from_db()
    return render(request, 'store/payment_failed.html', {
        'order': order,
        'page_title': 'Payment not completed',
        'esewa_demo': (
            settings.DEBUG and settings.ESEWA_SIMULATE
            and settings.ESEWA_PRODUCT_CODE == 'EPAYTEST'
        ),
    })


@require_POST
@never_cache
def esewa_demo_confirm(request, order_number):
    """Sandbox-only shortcut to the success page when an eSewa login isn't available.

    The real eSewa RC gateway requires an eSewa test account to sign in, which a
    classroom/marker demo run may not have. Against the EPAYTEST sandbox (where no
    real money moves) this lets the shopper confirm the order from our side after
    bouncing off eSewa, so the full paid/confirmed flow can still be shown. It is
    refused outside the sandbox, so a live deployment can never mark an order paid
    without a real, HMAC-verified eSewa payment.
    """
    order = _owned_order_or_404(request, order_number)
    if (
        not settings.DEBUG or not settings.ESEWA_SIMULATE
        or settings.ESEWA_PRODUCT_CODE != 'EPAYTEST'
        or order.payment_method != 'esewa'
    ):
        raise Http404
    if order.is_paid:
        return redirect('order_success', order_number=order.order_number)
    mark_order_paid(order, gateway='esewa', txn_id=f'SANDBOX-DEMO-{order.order_number}')
    messages.success(request, 'Payment received via eSewa. Your order is confirmed.')
    return redirect('order_success', order_number=order.order_number)



@never_cache
def order_success(request, order_number):
    order = _owned_order_or_404(request, order_number)
    if expire_order_reservation(order):
        messages.error(request, 'This unpaid order expired. Please place a new order.')
        return redirect('cart')
    order.refresh_from_db()
    return render(request, 'store/order_success.html', {
        'order': order,
        'page_title': 'Order Confirmed',
        'bank_details': (
            BANK_TRANSFER_DETAILS
            if order.payment_method == 'nay_bank' and order.status != 'cancelled'
            else None
        ),
    })


@login_required
def order_history(request):
    expire_stale_orders(limit=25)
    orders = Order.objects.filter(user=request.user).prefetch_related('items')
    status = request.GET.get('status')
    allowed_statuses = {value for value, _label in Order.STATUS_CHOICES}
    if status in allowed_statuses:
        orders = orders.filter(status=status)
    paginator = Paginator(orders, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'store/order_history.html', {
        'page_obj': page_obj, 'orders': page_obj.object_list,
        'page_title': 'My Orders',
    })


@login_required
def order_detail(request, order_number):
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product', 'status_history'),
        order_number=order_number
    )
    if order.user != request.user and not request.user.is_staff:
        raise Http404
    expire_order_reservation(order)
    order.refresh_from_db()
    return render(request, 'store/order_detail.html', {'order': order, 'page_title': f'Order {order.order_number}'})


@login_required
def download_invoice(request, order_number):
    order = get_object_or_404(Order.objects.prefetch_related('items'), order_number=order_number)
    if order.user != request.user and not request.user.is_staff:
        raise Http404
    pdf = build_invoice_pdf(order)
    if pdf:
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="invoice-{order.order_number}.pdf"'
        return response
    return render(request, 'store/invoice.html', {'order': order})


@require_POST
@login_required
def cancel_order(request, order_number):
    with transaction.atomic():
        order = get_object_or_404(
            Order.objects.select_for_update(),
            order_number=order_number, user=request.user,
        )
        if order.can_cancel:
            release_order_inventory(order)
            order.status = 'cancelled'
            order.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Order cancelled.')
        else:
            messages.error(request, 'Paid or completed orders must be handled by support.')
    return redirect('order_detail', order_number=order_number)


@require_POST
@login_required
def reorder(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    manager = CartManager(request)
    for item in order.items.all():
        if item.product and item.product.is_active:
            try:
                manager.add(item.product, quantity=item.quantity, variant=item.variant)
            except ValidationError:
                messages.warning(request, f'"{item.product_name}" is no longer available in that quantity.')
    messages.success(request, 'Available items were added back to your cart.')
    return redirect('cart')



@login_required
def profile(request):
    orders = Order.objects.filter(user=request.user)
    context = {
        'order_count': orders.count(),
        'pending_count': orders.filter(status='pending').count(),
        'completed_count': orders.filter(payment_status='paid').count(),
        'wishlist_count': Wishlist.objects.filter(user=request.user).first().items.count() if Wishlist.objects.filter(user=request.user).exists() else 0,
        'recent_orders': orders.prefetch_related('items')[:5],
        'page_title': 'My Account',
    }
    return render(request, 'store/profile.html', context)


@login_required
def profile_edit(request):
    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated.')
            return redirect('profile')
    else:
        form = UserProfileForm(instance=request.user)
    return render(request, 'store/profile_edit.html', {'form': form, 'page_title': 'Edit Profile'})


@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Password changed successfully.')
            return redirect('profile')
    else:
        form = PasswordChangeForm(request.user)
    return render(request, 'store/change_password.html', {'form': form, 'page_title': 'Change Password'})


@login_required
def my_reviews(request):
    reviews = request.user.reviews.select_related('product')
    return render(request, 'store/my_reviews.html', {'reviews': reviews, 'page_title': 'My Reviews'})



@login_required
def wishlist_view(request):
    wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
    items = wishlist.items.select_related('product').prefetch_related('product__images')
    return render(request, 'store/wishlist.html', {'wishlist': wishlist, 'items': items, 'page_title': 'My Wishlist'})


@require_POST
@login_required
def wishlist_toggle(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_active=True)
    wishlist, _ = Wishlist.objects.get_or_create(user=request.user)
    item = wishlist.items.filter(product=product).first()
    if item:
        item.delete()
        added = False
        message = 'Removed from wishlist.'
    else:
        WishlistItem.objects.create(wishlist=wishlist, product=product)
        added = True
        message = 'Added to wishlist.'
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'added': added, 'message': message})
    messages.success(request, message)
    return redirect(safe_redirect_target(request, reverse('wishlist')))


@require_POST
@login_required
def wishlist_remove(request, item_id):
    item = get_object_or_404(WishlistItem, id=item_id, wishlist__user=request.user)
    item.delete()
    messages.success(request, 'Removed from wishlist.')
    return redirect('wishlist')


@require_POST
@login_required
def wishlist_move_to_cart(request, item_id):
    item = get_object_or_404(WishlistItem, id=item_id, wishlist__user=request.user)
    if item.product.is_in_stock:
        try:
            CartManager(request).add(item.product, quantity=1, variant=item.variant)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            item.delete()
            messages.success(request, 'Moved to cart.')
    else:
        messages.error(request, 'Product is out of stock.')
    return redirect('wishlist')



@require_POST
@login_required
@rate_limit('review-submit', limit=10, window=3600)
def add_review(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    form = ReviewForm(request.POST, user=request.user, product=product)
    if form.is_valid():
        review = form.save(commit=False)
        review.user = request.user
        review.product = product
        if product.order_items.filter(order__user=request.user, order__payment_status='paid').exists():
            review.is_verified_purchase = True
        review.save()
        messages.success(request, 'Thank you! Your review is awaiting approval.')
    else:
        messages.error(request, 'Could not submit review. You may have already reviewed this product.')
    return redirect('product_detail', slug=slug)


@require_POST
@login_required
@rate_limit('review-helpful', limit=60, window=3600)
def mark_review_helpful(request, review_id):
    review = get_object_or_404(Review, id=review_id, is_approved=True)
    _vote, created = ReviewHelpfulVote.objects.get_or_create(
        review=review, user=request.user
    )
    if created:
        Review.objects.filter(pk=review.pk).update(helpful_votes=F('helpful_votes') + 1)
    review.refresh_from_db(fields=['helpful_votes'])
    return JsonResponse({
        'success': True,
        'added': created,
        'helpful_votes': review.helpful_votes,
    })



@rate_limit('register', limit=5, window=3600)
@sensitive_post_parameters('password1', 'password2')
def register(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        form = CustomRegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False
            user.save()
            send_email_verification(user)
            messages.success(
                request,
                'Account created. Check your email and verify the address before signing in.',
            )
            return redirect('login')
    else:
        form = CustomRegisterForm()
    return render(request, 'registration/register.html', {'form': form})


@require_http_methods(['GET', 'POST'])
@never_cache
def verify_email(request, uidb64, token):
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is None or not default_token_generator.check_token(user, token):
        messages.error(request, 'This verification link is invalid or has expired.')
        return redirect('login')
    if request.method != 'POST':
        return render(request, 'registration/verify_email_confirm.html', {
            'verification_user': user,
            'uidb64': uidb64,
            'token': token,
            'page_title': 'Verify Email',
        })
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=['is_active'])
    login(request, user, backend='store.backends.EmailOrUsernameModelBackend')
    send_welcome_email(user)
    messages.success(request, 'Email verified. Your account is now active.')
    return redirect('home')


@require_POST
@rate_limit('resend-verification', limit=5, window=3600)
def resend_verification(request):
    email = (request.POST.get('email') or '').strip().lower()[:254]
    user = User.objects.filter(email__iexact=email, is_active=False).first()
    if user:
        send_email_verification(user)
    # The response is intentionally identical for existing/non-existing emails.
    messages.info(
        request,
        'If an unverified account exists for that address, a new link has been sent.',
    )
    return redirect('login')


@method_decorator(sensitive_post_parameters('password'), name='dispatch')
class SecureLoginView(LoginView):
    template_name = 'registration/login.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.POST.get('remember'):
            self.request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        else:
            self.request.session.set_expiry(0)
        return response


@method_decorator(rate_limit('password-reset', limit=5, window=3600), name='dispatch')
@method_decorator(sensitive_post_parameters('email'), name='dispatch')
class CustomPasswordResetView(PasswordResetView):
    template_name = 'registration/password_reset_form.html'
    success_url = reverse_lazy('password_reset_done')
    email_template_name = 'registration/password_reset_email.html'

    def form_valid(self, form):
        self.request.session['reset_email'] = form.cleaned_data.get('email')
        site_url = urlsplit(settings.SITE_BASE_URL)
        form.save(
            use_https=site_url.scheme == 'https',
            token_generator=self.token_generator,
            from_email=self.from_email,
            email_template_name=self.email_template_name,
            subject_template_name=self.subject_template_name,
            request=self.request,
            domain_override=site_url.netloc,
            html_email_template_name=self.html_email_template_name,
            extra_email_context=self.extra_email_context,
        )
        return redirect(self.get_success_url())


class CustomPasswordResetDoneView(PasswordResetDoneView):
    template_name = 'registration/password_reset_done.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['reset_email'] = self.request.session.get('reset_email', 'your email address')
        return context


class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = 'registration/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class CustomPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = 'registration/password_reset_complete.html'



def about(request):
    return render(request, 'store/pages/about.html', {'page_title': 'About Us'})


@rate_limit('contact-html', limit=5, window=60)
def contact(request):
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if form.is_valid():
            msg = form.save()
            send_templated_email(
                f'New contact message: {msg.subject}',
                'emails/contact_notification.html',
                {'contact': msg},
                [SiteSettings.get_settings().email or settings.DEFAULT_FROM_EMAIL],
            )
            messages.success(request, 'Thank you! We will get back to you soon.')
            return redirect('contact')
    else:
        form = ContactForm()
    return render(request, 'store/pages/contact.html', {'form': form, 'page_title': 'Contact Us'})


def faq(request):
    return render(request, 'store/pages/faq.html', {'page_title': 'FAQ'})


def privacy(request):
    return render(request, 'store/pages/privacy.html', {'page_title': 'Privacy Policy'})


def terms(request):
    return render(request, 'store/pages/terms.html', {'page_title': 'Terms & Conditions'})


def refund_policy(request):
    return render(request, 'store/pages/refund_policy.html', {'page_title': 'Refund Policy'})


@require_POST
@rate_limit('newsletter', limit=10, window=3600)
def newsletter_subscribe(request):
    form = NewsletterForm(request.POST)
    if form.is_valid():
        form.save()
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'message': 'Subscribed successfully!'})
        messages.success(request, 'Subscribed to our newsletter!')
    else:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            errors = form.errors.get('email', ['Invalid email.'])[0]
            return JsonResponse({'success': False, 'message': errors}, status=400)
        messages.error(request, 'Could not subscribe. You may already be subscribed.')
    return redirect(safe_redirect_target(request, reverse('home')))


def error_404(request, exception):
    return render(request, 'store/errors/404.html', status=404)


def error_500(request):
    return render(request, 'store/errors/500.html', status=500)


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /checkout/",
        "Disallow: /cart/",
        "Disallow: /orders/",
        "Disallow: /profile/",
        f"Sitemap: {absolute_site_url('/sitemap.xml')}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")
