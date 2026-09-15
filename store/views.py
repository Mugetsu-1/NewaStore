import base64
import json
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import PasswordResetView, PasswordResetDoneView, PasswordResetConfirmView, PasswordResetCompleteView
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Avg, Count, F, Sum
from django.http import JsonResponse, HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods
from django.db.models import Prefetch

from .payments import (
    is_stripe_configured, create_stripe_payment_intent, retrieve_stripe_payment_intent,
    is_paypal_configured, create_paypal_order, capture_paypal_order, PayPalError,
    get_simulated_gateway, is_simulated_gateway, BANK_TRANSFER_DETAILS,
)
from .models import (
    Product, Category, Tag, ProductVariant, ProductImage, Review,
    Coupon, CouponUsage, Address, Wishlist, WishlistItem,
    Order, OrderItem, OrderStatusHistory, ShippingMethod,
    NewsletterSubscriber, ContactMessage, SiteSettings, CartItem, Cart,
)
from .forms import (
    CustomRegisterForm, UserProfileForm, ReviewForm, AddressForm,
    CheckoutForm, CouponApplyForm, NewsletterForm, ContactForm,
    ProductSearchForm, OrderStatusUpdateForm,
)
from .cart import CartManager
from .utils import (
    calculate_tax, send_order_confirmation, send_order_status_update,
    build_invoice_pdf, get_client_ip, send_templated_email,
    send_welcome_email, mark_order_paid,
)

User = get_user_model()


# ============================================================
# CATALOG
# ============================================================

def home(request):
    products = Product.objects.filter(is_active=True).select_related('category')\
        .prefetch_related('images', 'tags')
    featured = products.filter(is_featured=True)[:8]

    # Hero banner: discounted featured games first, then the rest. Prefer the
    # HD screenshots (gallery sort_order > 0) as full-bleed background art.
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
            'bg_url': (bg or imgs[0]).src_url,
            'art_url': imgs[0].src_url,
        })

    new_arrivals = products.order_by(F('published_at').desc(nulls_last=True))[:8]
    deals = products.filter(discount_price__isnull=False).order_by('-discount_price')[:8]
    categories = Category.objects.filter(is_active=True, parent__isnull=True).annotate(
        num_products=Count('products', filter=Q(products__is_active=True))
    )[:9]
    best_sellers = products.annotate(order_count=Count('order_items'))\
        .filter(order_count__gt=0).order_by('-order_count')[:8]
    genres = Tag.objects.annotate(
        product_count=Count('products', filter=Q(products__is_active=True))
    ).filter(product_count__gt=0).order_by('-product_count')[:12]

    context = {
        'featured_products': featured,
        'hero_slides': hero_slides,
        'new_arrivals': new_arrivals,
        'deal_products': deals,
        'categories': categories,
        'best_sellers': best_sellers,
        'genres': genres,
        'page_title': 'Home',
    }
    return render(request, 'store/home.html', context)


def product_list(request):
    products = Product.objects.filter(is_active=True).select_related('category')\
        .prefetch_related('images', 'tags')
    form = ProductSearchForm(request.GET)

    category_slug = request.GET.get('category')
    if category_slug:
        products = products.filter(category__slug=category_slug)

    tag_slug = request.GET.get('tag')
    if tag_slug:
        products = products.filter(tags__slug=tag_slug)

    q = request.GET.get('q')
    if q:
        # Name has a pg_trgm GIN index — keep the heavy fields out of the
        # search predicate so it stays fast at 60k+ rows.
        products = products.filter(
            Q(name__icontains=q) |
            Q(short_description__icontains=q) |
            Q(tags__name__icontains=q) |
            Q(category__name__icontains=q)
        ).distinct()

        # Zero local hits -> try a live import from CheapShark so any
        # findable title (Sekiro, Elden Ring, ...) appears immediately.
        if len(q.strip()) >= 3 and not products.exists():
            try:
                from .importers import import_search_query
                if import_search_query(q.strip()) > 0:
                    products = products.filter(  # re-run the same predicate
                        Q(name__icontains=q) |
                        Q(short_description__icontains=q) |
                        Q(tags__name__icontains=q) |
                        Q(category__name__icontains=q)
                    ).distinct()
                    messages.info(request,
                                  f'Imported live results for "{q}" from the '
                                  f'international stores.')
            except Exception:
                pass  # never break search because an API is down

    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    if min_price:
        products = products.filter(price__gte=min_price)
    if max_price:
        products = products.filter(price__lte=max_price)

    if request.GET.get('in_stock_only'):
        products = products.filter(stock_quantity__gt=0)
    if request.GET.get('on_sale_only'):
        products = products.filter(discount_price__isnull=False)

    sort_by = request.GET.get('sort_by', '-created_at')
    allowed_sorts = ['-created_at', 'name', '-name', 'price', '-price', '-is_featured', 'newest']
    if sort_by in allowed_sorts:
        products = products.order_by(sort_by)

    paginator = Paginator(products, 24)
    page_obj = paginator.get_page(request.GET.get('page'))

    genres = Tag.objects.annotate(
        product_count=Count('products', filter=Q(products__is_active=True))
    ).filter(product_count__gt=0).order_by('name')

    context = {
        'page_obj': page_obj,
        'products': page_obj.object_list,
        'form': form,
        'total_count': products.count(),
        'genres': genres,
        'page_title': 'Shop',
    }
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return render(request, 'store/partials/product_grid_fragment.html', context)
    return render(request, 'store/product_list.html', context)


def category_detail(request, slug):
    category = get_object_or_404(Category, slug=slug, is_active=True)
    products = Product.objects.filter(is_active=True, category=category).select_related('category').prefetch_related('images')

    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    if min_price:
        products = products.filter(price__gte=min_price)
    if max_price:
        products = products.filter(price__lte=max_price)

    sort_by = request.GET.get('sort_by', '-created_at')
    if sort_by in ['-created_at', 'name', '-name', 'price', '-price']:
        products = products.order_by(sort_by)

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
        'total_count': products.count(),
    })


def search(request):
    return product_list(request)


def search_suggest(request):
    """Lightweight JSON endpoint for the header search dropdown."""
    q = (request.GET.get('q') or '').strip()
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
            'image': img.src_url if img else '',
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
    reviews = product.reviews.filter(is_approved=True).select_related('user')

    related = Product.objects.filter(is_active=True, category=product.category).exclude(pk=product.pk)[:4]
    if not related:
        related = Product.objects.filter(is_active=True).exclude(pk=product.pk)[:4]

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


# ============================================================
# CART
# ============================================================

def cart_view(request):
    cart = CartManager(request).cart
    coupon_form = CouponApplyForm()
    context = {
        'cart': cart,
        'items': cart.items.select_related('product', 'variant').all(),
        'coupon_form': coupon_form,
        'page_title': 'Shopping Cart',
    }
    return render(request, 'store/cart.html', context)


@require_POST
def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_active=True)

    if not product.is_in_stock:
        messages.error(request, f'"{product.name}" is out of stock.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    variant = None
    variant_id = request.POST.get('variant_id')
    if variant_id:
        variant = ProductVariant.objects.filter(id=variant_id, product=product, is_active=True).first()

    try:
        quantity = max(1, int(request.POST.get('quantity', 1)))
    except (TypeError, ValueError):
        quantity = 1

    manager = CartManager(request)
    manager.add(product, quantity=quantity, variant=variant)

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'cart_count': manager.count, 'message': f'"{product.name}" added to cart.'})

    messages.success(request, f'"{product.name}" added to your cart.')
    return redirect(request.META.get('HTTP_REFERER', 'cart'))


@require_POST
def update_cart(request, item_id):
    manager = CartManager(request)
    try:
        quantity = int(request.POST.get('quantity', 1))
    except (TypeError, ValueError):
        quantity = 1
    manager.update(item_id, quantity)

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
def apply_coupon(request):
    manager = CartManager(request)
    form = CouponApplyForm(request.POST)
    if form.is_valid():
        code = form.cleaned_data['code'].strip().upper()
        coupon = Coupon.objects.filter(code__iexact=code).first()
        if not coupon:
            messages.error(request, 'Invalid coupon code.')
        else:
            valid, msg = coupon.is_valid(request.user if request.user.is_authenticated else None, manager.subtotal)
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


# ============================================================
# CHECKOUT & PAYMENTS
# ============================================================

def _get_digital_shipping_method():
    """Free 'Digital Delivery' method used for digital-only carts."""
    method, _ = ShippingMethod.objects.get_or_create(
        name='Digital Delivery',
        defaults=dict(description='Game keys delivered by email — instantly',
                      price=Decimal('0'), estimated_days_min=0,
                      estimated_days_max=0, is_active=True, sort_order=0))
    return method


def _cart_all_digital(manager):
    """True when every item in the cart is a digital product."""
    items = list(manager.items)
    return bool(items) and all(not item.product.requires_shipping for item in items)


def checkout(request):
    manager = CartManager(request)
    if manager.is_empty:
        messages.warning(request, 'Your cart is empty.')
        return redirect('cart')

    user = request.user if request.user.is_authenticated else None
    all_digital = _cart_all_digital(manager)
    if request.method == 'POST':
        form = CheckoutForm(request.POST, user=request.user, cart=manager.cart)
        if form.is_valid():
            return _create_order_and_pay(request, form, manager)
    else:
        form = CheckoutForm(user=request.user, cart=manager.cart)

    context = {
        'form': form,
        'cart': manager.cart,
        'items': manager.items,
        'subtotal': manager.subtotal,
        'discount': manager.discount,
        'shipping_methods': ShippingMethod.objects.filter(is_active=True),
        'all_digital': all_digital,
        'digital_method': _get_digital_shipping_method() if all_digital else None,
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
    if data['shipping_option'] == CheckoutForm.SHIPPING_SAME_AS_BILLING:
        shipping = dict(billing)
    else:
        shipping = {
            'full_name': data['shipping_full_name'],
            'phone': data['shipping_phone'],
            'email': billing['email'],
            'address_line_1': data['shipping_address_line_1'],
            'address_line_2': data.get('shipping_address_line_2', ''),
            'city': data['shipping_city'],
            'state': data['shipping_state'],
            'postal_code': data['shipping_postal_code'],
            'country': data['shipping_country'],
        }

    # Digital-only carts always use the free Digital Delivery method
    if _cart_all_digital(manager):
        shipping_method = _get_digital_shipping_method()
    else:
        shipping_method = data['shipping_method']
    subtotal = manager.subtotal
    discount = manager.discount
    shipping_cost = shipping_method.price
    if shipping_method.free_shipping_threshold and subtotal >= shipping_method.free_shipping_threshold:
        shipping_cost = Decimal('0')

    taxable = subtotal - discount
    tax = calculate_tax(taxable)
    total = taxable + shipping_cost + tax

    with transaction.atomic():
        order = Order.objects.create(
            user=user,
            guest_email=billing['email'] if not user else '',
            guest_phone=billing['phone'] if not user else '',
            billing_address=billing,
            shipping_address=shipping,
            subtotal=subtotal,
            discount_amount=discount,
            shipping_cost=shipping_cost,
            tax_amount=tax,
            total=total,
            coupon=manager.cart.coupon,
            payment_method=data['payment_method'],
            shipping_method=shipping_method.name,
            notes=data.get('order_notes', ''),
            ip_address=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )

        for item in manager.items:
            OrderItem.objects.create(
                order=order,
                product=item.product,
                variant=item.variant,
                product_name=item.product.name,
                product_sku=item.product.sku,
                variant_name=item.variant.name if item.variant else '',
                variant_sku=item.variant.sku if item.variant else '',
                unit_price=item.unit_price,
                quantity=item.quantity,
            )
            if item.product.track_inventory:
                item.product.stock_quantity = max(0, item.product.stock_quantity - item.quantity)
                item.product.save(update_fields=['stock_quantity'])

        OrderStatusHistory.objects.create(order=order, status='pending', notes='Order placed')

        if order.coupon:
            if user:
                CouponUsage.objects.create(
                    coupon=order.coupon, user=user, order=order,
                    discount_amount=discount,
                )
            Coupon.objects.filter(pk=order.coupon.pk).update(used_count=F('used_count') + 1)

        if data.get('save_info') and user:
            _save_addresses(user, billing, shipping)

    # Clear the cart now that order exists
    manager.clear()

    send_order_confirmation(order)

    payment_method = data['payment_method']
    if is_simulated_gateway(payment_method):
        # eSewa / Khalti are demo wallets: render a local, self-contained
        # payment screen instead of redirecting to the real gateway.
        return _initiate_simulated_gateway(request, order, payment_method)
    elif payment_method == 'cod':
        order.status = 'confirmed'
        order.confirmed_at = timezone.now()
        order.save()
        return redirect('order_success', order_number=order.order_number)
    elif payment_method == 'nay_bank':
        # Offline settlement: keep the order pending until an admin verifies
        # the deposit. Bank details are shown on the order success page.
        messages.info(
            request,
            'Order placed. Complete the bank transfer using the details below so '
            'we can confirm your order.',
        )
        return redirect('order_success', order_number=order.order_number)
    elif payment_method == 'stripe':
        return redirect('stripe_checkout', order_number=order.order_number)
    elif payment_method == 'paypal':
        return redirect('paypal_checkout', order_number=order.order_number)
    else:
        # Any other gateway keeps the pending page
        return redirect('order_success', order_number=order.order_number)


def _save_addresses(user, billing, shipping):
    for addr_type, payload in [('billing', billing), ('shipping', shipping)]:
        if not user.addresses.filter(address_type=addr_type).exists():
            Address.objects.create(
                user=user, address_type=addr_type,
                full_name=payload['full_name'], phone=payload['phone'], email=payload['email'],
                address_line_1=payload['address_line_1'], address_line_2=payload.get('address_line_2', ''),
                city=payload['city'], state=payload['state'], postal_code=payload['postal_code'],
                country=payload['country'], is_default=True,
            )


def _initiate_simulated_gateway(request, order, gateway):
    """Render the in-app stand-in for eSewa / Khalti.

    No external request is made and no real money moves: the shopper sees a
    page laid out like the wallet's payment screen and chooses to confirm or
    decline a simulated transaction.
    """
    meta = get_simulated_gateway(gateway)
    if meta is None:
        raise Http404('Unknown payment gateway.')
    return render(request, 'store/simulated_gateway.html', {
        'order': order,
        'gateway': gateway,
        'meta': meta,
        'amount': order.total,
        'page_title': f'Pay with {meta["brand"]}',
    })


@require_http_methods(['GET', 'POST'])
def simulate_payment(request, order_number, gateway):
    """Handle the demo wallet screen: confirm (paid) or decline (failed)."""
    order = get_object_or_404(Order, order_number=order_number)
    meta = get_simulated_gateway(gateway)
    if meta is None:
        raise Http404('Unknown payment gateway.')

    if request.method == 'POST':
        outcome = request.POST.get('outcome', 'success')
        if outcome == 'success':
            txn_id = f'SIM-{uuid.uuid4().hex[:12].upper()}'
            mark_order_paid(order, gateway=gateway, txn_id=txn_id)
            messages.success(
                request,
                f'{meta["brand"]} payment confirmed (simulated) — '
                f'transaction {txn_id}.',
            )
            return redirect('order_success', order_number=order.order_number)

        if order.payment_status != 'paid':
            order.payment_status = 'failed'
            order.save(update_fields=['payment_status'])
        messages.error(
            request,
            f'{meta["brand"]} payment was declined (simulated). '
            'You can try again with another method.',
        )
        return redirect('payment_failed', order_number=order.order_number)

    return render(request, 'store/simulated_gateway.html', {
        'order': order,
        'gateway': gateway,
        'meta': meta,
        'amount': order.total,
        'page_title': f'Pay with {meta["brand"]}',
    })



# ============================================================
# STRIPE (Payment Intents + webhook)
# ============================================================

def _order_for_payment(request, order_number):
    """Orders are paid by link — the order number is the bearer token."""
    return get_object_or_404(Order, order_number=order_number)


def stripe_checkout(request, order_number):
    order = _order_for_payment(request, order_number)
    if not is_stripe_configured():
        messages.error(request, 'Stripe is not configured yet. Please choose another payment method.')
        return redirect('payment_failed', order_number=order.order_number)
    return render(request, 'store/stripe_form.html', {
        'order': order,
        'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
        'stripe_currency': getattr(settings, 'STRIPE_CURRENCY', 'usd'),
        'stripe_price_label': getattr(settings, 'STRIPE_PRICE_LABEL', 'USD $'),
        'page_title': 'Pay with Card',
    })


@require_POST
def stripe_create_intent(request, order_number):
    """JSON endpoint -> {client_secret} consumed by Stripe.js."""
    order = _order_for_payment(request, order_number)
    if not is_stripe_configured():
        return JsonResponse({'success': False, 'error': 'Stripe is not configured.'}, status=501)
    if order.payment_status == 'paid':
        return JsonResponse({'success': True, 'already_paid': True})
    try:
        intent = create_stripe_payment_intent(order)
    except Exception as exc:  # typed StripeError -> surface to the UI
        return JsonResponse({'success': False, 'error': str(exc)}, status=502)
    return JsonResponse({'success': True, 'client_secret': intent.get('client_secret')})


def stripe_success(request, order_number):
    """Stripe.js return URL — double-checks status server-side, then fulfills."""
    order = _order_for_payment(request, order_number)
    intent_id = request.GET.get('payment_intent', '')
    if intent_id:
        try:
            intent = retrieve_stripe_payment_intent(intent_id)
            if intent.get('status') == 'succeeded':
                mark_order_paid(order, gateway='stripe', txn_id=intent.get('id'))
                return redirect('order_success', order_number=order.order_number)
        except Exception:
            pass
    messages.error(request, 'Payment could not be verified yet. If you were charged, check your email for the receipt.')
    return redirect('payment_failed', order_number=order.order_number)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Webhook endpoint — fulfills orders when payment_intent.succeeded fires."""
    payload = request.body
    secret = getattr(settings, 'STRIPE_WEBHOOK_SECRET', '')
    if secret:
        try:
            import stripe
            stripe.api_key = settings.STRIPE_SECRET_KEY
            event = stripe.Webhook.construct_event(payload, request.META.get('HTTP_STRIPE_SIGNATURE', ''), secret)
        except Exception:
            return HttpResponse(status=400)
    else:
        try:
            event = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return HttpResponse(status=400)

    if event.get('type') == 'payment_intent.succeeded':
        pi = event['data']['object']
        order_number = (pi.get('metadata') or {}).get('order_number', '')
        order = Order.objects.filter(order_number=order_number).first()
        if order:
            mark_order_paid(order, gateway='stripe', txn_id=pi.get('id'))
    return HttpResponse(status=200)


# ============================================================
# PAYPAL (Orders v2 + webhook)
# ============================================================

def paypal_checkout(request, order_number):
    order = _order_for_payment(request, order_number)
    return render(request, 'store/paypal_form.html', {
        'order': order,
        'paypal_configured': is_paypal_configured(),
        'paypal_currency': getattr(settings, 'PAYPAL_CURRENCY', 'usd'),
        'paypal_price_label': getattr(settings, 'PAYPAL_PRICE_LABEL', 'USD $'),
        'page_title': 'Pay with PayPal',
    })


@require_POST
def paypal_create_order(request, order_number):
    """JSON endpoint -> {approval_url}; the checkout page redirects to PayPal."""
    order = _order_for_payment(request, order_number)
    if not is_paypal_configured():
        return JsonResponse({'success': False, 'error': 'PayPal is not configured.'}, status=501)
    if order.payment_status == 'paid':
        return JsonResponse({'success': True, 'already_paid': True})
    try:
        approval_url = create_paypal_order(
            order,
            request.build_absolute_uri(reverse('paypal_capture', args=[order.order_number])),
            request.build_absolute_uri(reverse('payment_failed', args=[order.order_number])),
        )
    except PayPalError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=502)
    return JsonResponse({'success': True, 'approval_url': approval_url})


def paypal_capture(request, order_number):
    """Return URL after approval — captures the order server-side and fulfills."""
    order = _order_for_payment(request, order_number)
    token = request.GET.get('token')
    if not token:
        messages.error(request, 'No PayPal payment token received.')
        return redirect('payment_failed', order_number=order.order_number)
    try:
        result = capture_paypal_order(token)
    except PayPalError as exc:
        Order.objects.filter(pk=order.pk).update(payment_status='failed')
        messages.error(request, f'PayPal capture failed: {exc}')
        return redirect('payment_failed', order_number=order.order_number)

    capture = result['purchase_units'][0]['payments']['captures'][0]
    mark_order_paid(order, gateway='paypal', txn_id=capture.get('id'))
    return redirect('order_success', order_number=order.order_number)


def _verify_paypal_webhook(request, event_body):
    """Verify a PayPal webhook with its transmission signature.

    Requires PAYPAL_WEBHOOK_ID + the `cryptography` package. Automatically
    skipped in development when PAYPAL_WEBHOOK_ID is empty.
    """
    webhook_id = getattr(settings, 'PAYPAL_WEBHOOK_ID', '')
    if not webhook_id:
        return True  # dev mode
    cert_url = request.META.get('HTTP_PAYPAL_CERT_URL', '')
    if not (cert_url.startswith('https://api-m.sandbox.paypal.com') or
            cert_url.startswith('https://api-m.paypal.com')):
        return False
    import base64
    import requests as _requests
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        return False
    try:
        cert_pem = _requests.get(cert_url, timeout=10).text
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        public_key = cert.public_key()
        transmission_id = request.META.get('HTTP_PAYPAL_TRANSMISSION_ID', '')
        transmission_time = request.META.get('HTTP_PAYPAL_TRANSMISSION_TIME', '')
        signature_b64 = request.META.get('HTTP_PAYPAL_TRANSMISSION_SIG', '')
        message = f"{transmission_id}|{transmission_time}|{webhook_id}|{event_body.decode('utf-8')}"
        public_key.verify(
            base64.b64decode(signature_b64),
            message.encode('utf-8'),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


@csrf_exempt
@require_POST
def paypal_webhook(request):
    """Receives PAYMENT.CAPTURE.COMPLETED notifications and fulfills orders."""
    try:
        event = json.loads(request.body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return HttpResponse(status=400)
    if not _verify_paypal_webhook(request, request.body):
        return HttpResponse(status=400)
    if event.get('event_type') == 'PAYMENT.CAPTURE.COMPLETED':
        resource = event.get('resource') or {}
        ref = ((resource.get('supplementary_data') or {}).get('related_ids') or {}).get('order_id', '')
        order = Order.objects.filter(order_number=ref).first()
        if order:
            mark_order_paid(order, gateway='paypal', txn_id=resource.get('id'))
    return HttpResponse('OK')


def esewa_verify(request):
    encoded_data = request.GET.get('data')
    if not encoded_data:
        messages.error(request, 'Payment verification failed.')
        return redirect('home')

    try:
        decoded = base64.b64decode(encoded_data).decode('utf-8')
        response = json.loads(decoded)
    except Exception:
        messages.error(request, 'Invalid payment response.')
        return redirect('home')

    transaction_uuid = response.get('transaction_uuid')
    order = Order.objects.filter(order_number=transaction_uuid).first()
    if not order:
        messages.error(request, 'Order not found.')
        return redirect('home')

    if response.get('status') == 'COMPLETE':
        order.payment_status = 'paid'
        order.payment_transaction_id = response.get('transaction_code', '')
        order.status = 'confirmed'
        order.confirmed_at = timezone.now()
        order.save()
        send_order_status_update(order)
        return redirect('order_success', order_number=order.order_number)

    order.payment_status = 'failed'
    order.save()
    return redirect('payment_failed', order_number=order.order_number)


def khalti_verify(request):
    order_number = request.GET.get('purchase_order_id')
    order = Order.objects.filter(order_number=order_number).first()
    if not order:
        messages.error(request, 'Order not found.')
        return redirect('home')

    status = request.GET.get('status', '').lower()
    if status == 'completed':
        order.payment_status = 'paid'
        order.payment_transaction_id = request.GET.get('transaction_id', '')
        order.status = 'confirmed'
        order.confirmed_at = timezone.now()
        order.save()
        send_order_status_update(order)
        return redirect('order_success', order_number=order.order_number)

    order.payment_status = 'failed'
    order.save()
    return redirect('payment_failed', order_number=order.order_number)


def payment_failed(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    return render(request, 'store/payment_failed.html', {'order': order, 'page_title': 'Payment Failed'})


# ============================================================
# ORDERS
# ============================================================

def order_success(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    if request.user.is_authenticated and order.user and order.user != request.user:
        raise Http404
    return render(request, 'store/order_success.html', {
        'order': order,
        'page_title': 'Order Confirmed',
        # Nay Bank Transfer orders stay pending until an admin verifies the
        # deposit, so surface the account details on this page.
        'bank_details': BANK_TRANSFER_DETAILS if order.payment_method == 'nay_bank' else None,
        'is_simulated': is_simulated_gateway(order.payment_method),
    })


@login_required
def order_history(request):
    orders = Order.objects.filter(user=request.user).prefetch_related('items')
    status = request.GET.get('status')
    if status:
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
    return render(request, 'store/order_detail.html', {'order': order, 'page_title': f'Order {order.order_number}'})


def order_tracking(request, order_number):
    order = get_object_or_404(Order.objects.prefetch_related('status_history'), order_number=order_number)
    return render(request, 'store/order_tracking.html', {'order': order, 'page_title': 'Track Order'})


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
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    if order.status in ['pending', 'confirmed', 'processing']:
        order.status = 'cancelled'
        order.save()
        OrderStatusHistory.objects.create(order=order, status='cancelled', notes='Cancelled by customer')
        messages.success(request, 'Order cancelled.')
    else:
        messages.error(request, 'This order can no longer be cancelled.')
    return redirect('order_detail', order_number=order_number)


@require_POST
@login_required
def reorder(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    manager = CartManager(request)
    for item in order.items.all():
        if item.product and item.product.is_active:
            manager.add(item.product, quantity=item.quantity, variant=item.variant)
    messages.success(request, 'Items added back to your cart.')
    return redirect('cart')


# ============================================================
# ACCOUNT
# ============================================================

@login_required
def profile(request):
    orders = Order.objects.filter(user=request.user)
    context = {
        'order_count': orders.count(),
        'pending_count': orders.filter(status='pending').count(),
        'completed_count': orders.filter(status='delivered').count(),
        'wishlist_count': Wishlist.objects.filter(user=request.user).first().items.count() if Wishlist.objects.filter(user=request.user).exists() else 0,
        'recent_orders': orders[:5],
        'addresses': request.user.addresses.all()[:3],
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
def address_list(request):
    addresses = request.user.addresses.all()
    return render(request, 'store/address_list.html', {'addresses': addresses, 'page_title': 'My Addresses'})


@login_required
def address_create(request):
    if request.method == 'POST':
        form = AddressForm(request.POST)
        if form.is_valid():
            address = form.save(commit=False)
            address.user = request.user
            address.save()
            messages.success(request, 'Address added.')
            return redirect('address_list')
    else:
        form = AddressForm()
    return render(request, 'store/address_form.html', {'form': form, 'page_title': 'Add Address'})


@login_required
def address_edit(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    if request.method == 'POST':
        form = AddressForm(request.POST, instance=address)
        if form.is_valid():
            form.save()
            messages.success(request, 'Address updated.')
            return redirect('address_list')
    else:
        form = AddressForm(instance=address)
    return render(request, 'store/address_form.html', {'form': form, 'address': address, 'page_title': 'Edit Address'})


@require_POST
@login_required
def address_delete(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    address.delete()
    messages.success(request, 'Address deleted.')
    return redirect('address_list')


@require_POST
@login_required
def address_set_default(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    Address.objects.filter(user=request.user, address_type=address.address_type, is_default=True).update(is_default=False)
    address.is_default = True
    address.save()
    messages.success(request, 'Default address updated.')
    return redirect('address_list')


@login_required
def my_reviews(request):
    reviews = request.user.reviews.select_related('product')
    return render(request, 'store/my_reviews.html', {'reviews': reviews, 'page_title': 'My Reviews'})


# ============================================================
# WISHLIST
# ============================================================

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
    return redirect(request.META.get('HTTP_REFERER', 'wishlist'))


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
        CartManager(request).add(item.product, quantity=1, variant=item.variant)
        item.delete()
        messages.success(request, 'Moved to cart.')
    else:
        messages.error(request, 'Product is out of stock.')
    return redirect('wishlist')


# ============================================================
# REVIEWS
# ============================================================

@require_POST
@login_required
def add_review(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    form = ReviewForm(request.POST, user=request.user, product=product)
    if form.is_valid():
        review = form.save(commit=False)
        review.user = request.user
        review.product = product
        if product.order_items.filter(order__user=request.user, order__status='delivered').exists():
            review.is_verified_purchase = True
        review.save()
        messages.success(request, 'Thank you! Your review is awaiting approval.')
    else:
        messages.error(request, 'Could not submit review. You may have already reviewed this product.')
    return redirect('product_detail', slug=slug)


@require_POST
@login_required
def mark_review_helpful(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    review.helpful_votes = F('helpful_votes') + 1
    review.save(update_fields=['helpful_votes'])
    return JsonResponse({'success': True, 'helpful_votes': review.helpful_votes})


# ============================================================
# AUTH
# ============================================================

def register(request):
    if request.user.is_authenticated:
        return redirect('home')
    if request.method == 'POST':
        form = CustomRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            if user.email:
                send_welcome_email(user)
            messages.success(request, f'Welcome, {user.first_name or user.username}!')
            return redirect('home')
    else:
        form = CustomRegisterForm()
    return render(request, 'registration/register.html', {'form': form})


class CustomPasswordResetView(PasswordResetView):
    template_name = 'registration/password_reset_form.html'
    success_url = reverse_lazy('password_reset_done')
    email_template_name = 'registration/password_reset_email.html'

    def form_valid(self, form):
        self.request.session['reset_email'] = form.cleaned_data.get('email')
        return super().form_valid(form)


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


# ============================================================
# STATIC PAGES
# ============================================================

def about(request):
    return render(request, 'store/pages/about.html', {'page_title': 'About Us'})


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


def shipping_returns(request):
    return render(request, 'store/pages/shipping_returns.html', {'page_title': 'Shipping & Returns'})


@require_POST
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
    return redirect(request.META.get('HTTP_REFERER', 'home'))


def error_404(request, exception):
    return render(request, 'store/errors/404.html', status=404)


def error_500(request):
    return render(request, 'store/errors/500.html', status=500)


def maintenance(request):
    settings_obj = SiteSettings.get_settings()
    return render(request, 'store/maintenance.html', {'site_settings': settings_obj})


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /checkout/",
        "Disallow: /cart/",
        "Disallow: /orders/",
        "Disallow: /profile/",
        f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")
