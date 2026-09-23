import base64
import json
from decimal import Decimal, InvalidOperation

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
from django.views.decorators.http import require_POST
from django.db.models import Prefetch

from .payments import (
    is_esewa_configured, build_esewa_form, verify_esewa_signature,
    BANK_TRANSFER_DETAILS,
)
from .models import (
    Product, Category, Tag, ProductVariant, ProductImage, Review,
    Coupon, CouponUsage, Wishlist, WishlistItem,
    Order, OrderItem, OrderStatusHistory,
    NewsletterSubscriber, ContactMessage, SiteSettings, CartItem, Cart,
    SavedBillingDetail,
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
    send_welcome_email, mark_order_paid,
)

User = get_user_model()



def home(request):
    products = Product.objects.filter(is_active=True).select_related('category')\
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
        product_count=Count('products', filter=Q(products__is_active=True))
    ).filter(product_count__gt=0).order_by('-product_count')[:12]

    hero_covers = [
        img.card_image_url
        for img in (ProductImage.objects.filter(product__is_active=True)
                    .exclude(thumbnail='').only('thumbnail', 'image')
                    .order_by('-id')[:24])
        if img.card_image_url
    ]
    hero_collage = [hero_covers[i::3] for i in range(3)] if len(hero_covers) >= 6 else []
    hero_stats = {
        'games': f"{products.count():,}",
        'deals': f"{products.filter(discount_price__isnull=False).count():,}",
        'genres': f"{Tag.objects.filter(products__is_active=True).distinct().count():,}",
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
        products = products.filter(
            Q(name__icontains=q) |
            Q(short_description__icontains=q) |
            Q(tags__name__icontains=q) |
            Q(category__name__icontains=q)
        ).distinct()

        if len(q.strip()) >= 3 and not products.exists():
            try:
                from .importers import import_search_query
                if import_search_query(q.strip()) > 0:
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
                pass

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

    sort_by = request.GET.get('sort_by') or 'featured'
    sort_map = {
        'featured': ('-tier', '-is_featured', '-created_at'),
        '-created_at': ('-created_at',),
        'newest': ('-tier', F('published_at').desc(nulls_last=True)),
        'name': ('name',),
        '-name': ('-name',),
        'price': ('price',),
        '-price': ('-price',),
        '-is_featured': ('-is_featured', '-tier', '-created_at'),
    }
    products = products.order_by(*sort_map.get(sort_by, sort_map['featured']))

    paginator = Paginator(products, 24)
    page_obj = paginator.get_page(request.GET.get('page'))

    genres = Tag.objects.annotate(
        product_count=Count('products', filter=Q(products__is_active=True))
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

    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    if min_price:
        products = products.filter(price__gte=min_price)
    if max_price:
        products = products.filter(price__lte=max_price)

    sort_by = request.GET.get('sort_by') or 'featured'
    cat_sorts = {
        'featured': ('-tier', '-is_featured', '-created_at'),
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



def checkout(request):
    manager = CartManager(request)
    if manager.is_empty:
        messages.warning(request, 'Your cart is empty.')
        return redirect('cart')

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
    subtotal = manager.subtotal
    discount = manager.discount
    taxable = subtotal - discount
    tax = calculate_tax(taxable)
    total = taxable + tax

    with transaction.atomic():
        order = Order.objects.create(
            user=user,
            guest_email=billing['email'] if not user else '',
            guest_phone=billing['phone'] if not user else '',
            billing_address=billing,
            subtotal=subtotal,
            discount_amount=discount,
            tax_amount=tax,
            total=total,
            coupon=manager.cart.coupon,
            payment_method=data['payment_method'],
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

    manager.clear()

    send_order_confirmation(order)

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


def esewa_checkout(request, order_number):
    """Render the signed auto-submit form that POSTs the order to eSewa."""
    order = get_object_or_404(Order, order_number=order_number)
    if order.payment_status == 'paid':
        return redirect('order_success', order_number=order.order_number)
    if not is_esewa_configured():
        messages.error(request, 'eSewa is not available right now. Please choose another method.')
        return redirect('payment_failed', order_number=order.order_number)
    form = build_esewa_form(
        order,
        success_url=request.build_absolute_uri(reverse('esewa_verify')),
        failure_url=request.build_absolute_uri(reverse('payment_failed', args=[order.order_number])),
    )
    return render(request, 'store/esewa_form.html', {
        'order': order,
        'esewa_action': form['action'],
        'esewa_fields': form['fields'],
        'page_title': 'Redirecting to eSewa',
    })


def esewa_verify(request):
    """Success callback — trust only eSewa's signed, server-recomputed response.

    eSewa returns the signed fields as base64 JSON in ``?data=``. The order is
    fulfilled only when the HMAC signature verifies, the status is COMPLETE, and
    the returned amount matches the order total to the paisa.
    """
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

    order = Order.objects.filter(order_number=response.get('transaction_uuid')).first()
    if not order:
        messages.error(request, 'Order not found.')
        return redirect('home')

    try:
        paid = Decimal(str(response.get('total_amount', '0')).replace(',', ''))
    except (InvalidOperation, TypeError):
        paid = Decimal('0')

    if (verify_esewa_signature(response)
            and response.get('status') == 'COMPLETE'
            and paid == order.total.quantize(Decimal('0.01'))):
        mark_order_paid(order, gateway='esewa', txn_id=response.get('transaction_code', ''))
        return redirect('order_success', order_number=order.order_number)

    if order.payment_status != 'paid':
        Order.objects.filter(pk=order.pk).update(payment_status='failed')
    messages.error(request, 'eSewa payment could not be verified.')
    return redirect('payment_failed', order_number=order.order_number)


def payment_failed(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    return render(request, 'store/payment_failed.html', {'order': order, 'page_title': 'Payment Failed'})



def order_success(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    if request.user.is_authenticated and order.user and order.user != request.user:
        raise Http404
    return render(request, 'store/order_success.html', {
        'order': order,
        'page_title': 'Order Confirmed',
        'bank_details': BANK_TRANSFER_DETAILS if order.payment_method == 'nay_bank' else None,
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



@login_required
def profile(request):
    orders = Order.objects.filter(user=request.user)
    context = {
        'order_count': orders.count(),
        'pending_count': orders.filter(status='pending').count(),
        'completed_count': orders.filter(payment_status='paid').count(),
        'wishlist_count': Wishlist.objects.filter(user=request.user).first().items.count() if Wishlist.objects.filter(user=request.user).exists() else 0,
        'recent_orders': orders[:5],
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



@require_POST
@login_required
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
def mark_review_helpful(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    review.helpful_votes = F('helpful_votes') + 1
    review.save(update_fields=['helpful_votes'])
    return JsonResponse({'success': True, 'helpful_votes': review.helpful_votes})



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


def refund_policy(request):
    return render(request, 'store/pages/refund_policy.html', {'page_title': 'Refund Policy'})


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
