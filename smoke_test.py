"""Manual smoke test: GET every major route and report status codes."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'newastore.settings')
django.setup()

from django.test import Client
from django.contrib.auth.models import User
from store.models import Product, Category, Order, ShippingMethod

c = Client(raise_request_exception=True)

product = Product.objects.first()
category = Category.objects.first()
order = Order.objects.first()
user = User.objects.filter(username='demo').first()

urls = [
    '/', '/shop/', '/search/?q=game', f'/product/{product.slug}/',
    f'/category/{category.slug}/', '/cart/', '/checkout/',
    '/about/', '/contact/', '/faq/', '/privacy/', '/terms/', '/shipping-returns/',
    '/login/', '/register/', '/password-reset/',
    '/sitemap.xml', '/robots.txt',
]
if order:
    urls += [f'/order/success/{order.order_number}/', f'/orders/{order.order_number}/track/']

# Phase-1/3 additions: API + payment pages
urls += ['/api/health/', '/api/search/?q=game', '/api/browse/', '/api/genres/']
if product:
    urls += [f'/api/games/{product.slug}/']
if order:
    urls += [f'/payment/stripe/{order.order_number}/', f'/payment/paypal/{order.order_number}/']
urls += ['/webhooks/stripe/', '/webhooks/paypal/', f'/payment/stripe/FAKE/intent/']

authed = ['/profile/', '/profile/edit/', '/profile/password/', '/reviews/',
          '/addresses/', '/addresses/add/', '/wishlist/', '/orders/']
if order:
    authed += [f'/orders/{order.order_number}/', f'/orders/{order.order_number}/invoice/']

results = []
for u in urls:
    try:
        r = c.get(u)
        results.append((r.status_code, u))
    except Exception as e:
        results.append(('ERR', f'{u} -> {type(e).__name__}: {e}'))

if user:
    c.force_login(user)
    for u in authed:
        try:
            r = c.get(u)
            results.append((r.status_code, u))
        except Exception as e:
            results.append(('ERR', f'{u} -> {type(e).__name__}: {e}'))

# AJAX grid fragment (the infinite-scroll / filter endpoint over /shop/)
try:
    r = c.get('/shop/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    results.append((200 if r.status_code == 200 and b'product-grid' in r.content else r.status_code, '/shop/ (AJAX fragment)'))
except Exception as e:
    results.append(('ERR', f'/shop/ (AJAX) -> {type(e).__name__}: {e}'))

# Admin
admin = User.objects.filter(is_superuser=True).first()
if admin:
    c.force_login(admin)
    for u in ['/admin/', '/admin/store/product/', '/admin/store/order/']:
        try:
            results.append((c.get(u).status_code, u))
        except Exception as e:
            results.append(('ERR', f'{u} -> {type(e).__name__}: {e}'))

print('\n--- SMOKE RESULTS ---')
bad = 0
for status, u in results:
    # 405 is expected for POST-only routes (webhooks, create-intent)
    flag = 'OK ' if status in (200, 301, 302, 405) else 'BAD'
    if flag == 'BAD':
        bad += 1
    print(f'[{flag}] {status}  {u}')
print(f'\n{len(results)} routes checked, {bad} problems.')
