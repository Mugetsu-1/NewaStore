import os
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path('.') / '.env')
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'newastore.settings')
django.setup()
from django.test import Client
from django.contrib.auth.models import User
from store.models import Product, Order
from django.core.mail import send_mail
import re

print("=== 1. EMAIL TEST ===")
from django.conf import settings
print(f"Email backend: {settings.EMAIL_BACKEND}")
print(f"Email host: {settings.EMAIL_HOST}")
print(f"Email user: {settings.EMAIL_HOST_USER}")
print(f"Email password set: {bool(settings.EMAIL_HOST_PASSWORD)}")
try:
    mail_sent = send_mail('Test', 'Test email', settings.DEFAULT_FROM_EMAIL, ['newastore8@gmail.com'], fail_silently=False)
    print(f"Email sent: {mail_sent}")
except Exception as e:
    print(f"Email error: {e}")

print()
print("=== 2. CHECKOUT FLOW TEST ===")

user, created = User.objects.get_or_create(
    username='checkout_test_user',
    defaults={'email': 'checkout@test.com', 'first_name': 'Test', 'last_name': 'User'}
)
if created:
    user.set_password('testpass123')
    user.save()

client = Client()
client.force_login(user)

product = Product.objects.filter(is_active=True).first()
if product:
    print(f"Product: {product.name} (ID: {product.id})")
    print(f"Stock: {product.stock_quantity}, Digital: {product.is_digital}")
    
    cart_resp = client.post(f'/cart/add/{product.id}/', {'quantity': 1})
    print(f"Add to cart: {cart_resp.status_code}")
    
    checkout_resp = client.get('/checkout/')
    print(f"Checkout page: {checkout_resp.status_code}")
    
    body = checkout_resp.content.decode()
    
    payment_methods = re.findall(r'name="payment_method"[^>]*value="([^"]+)"', body)
    print(f"Payment methods in form: {payment_methods}")

    if 'errorlist' in body:
        errors = re.findall(r'<ul class="errorlist">(.*?)</ul>', body, re.DOTALL)
        print(f"Form errors found: {errors}")

    csrf_match = re.search(r'name="csrfmiddlewaretoken"[^>]*value="([^"]+)"', body)
    csrf_token = csrf_match.group(1) if csrf_match else ''

    form_data = {
        'csrfmiddlewaretoken': csrf_token,
        'billing_full_name': 'Test User',
        'billing_phone': '9800000000',
        'billing_email': 'test@example.com',
        'billing_address_line_1': '123 Test Street',
        'billing_city': 'Kathmandu',
        'billing_state': 'Bagmati',
        'billing_postal_code': '44600',
        'billing_country': 'Nepal',
        'payment_method': 'nay_bank',
        'terms_accepted': 'on',
    }

    submit_resp = client.post('/checkout/', form_data)
    print(f"Submit checkout: {submit_resp.status_code}")
    if hasattr(submit_resp, 'url'):
        print(f"Redirect to: {submit_resp.url}")
    else:
        body2 = submit_resp.content.decode()
        if 'errorlist' in body2:
            errs = re.findall(r'<ul class="errorlist">(.*?)</ul>', body2, re.DOTALL)
            print(f"Submit errors: {errs}")
        order_found = re.search(r'Order Number:\s*([A-Z0-9-]+)', body2)
        if order_found:
            print(f"Order number: {order_found.group(1)}")
    
    orders = Order.objects.filter(user=user)
    print(f"Orders created: {orders.count()}")
    for o in orders:
        print(f"  - {o.order_number}: {o.status} / {o.payment_method}")
else:
    print("No active products found!")

print()
print("=== 3. ADMIN ACCESS TEST ===")

admin_user = User.objects.filter(is_superuser=True).first()
if admin_user:
    print(f"Admin user: {admin_user.username}")
    print(f"Is staff: {admin_user.is_staff}")
    print(f"Has password: {admin_user.has_usable_password()}")
    
    admin_client = Client()
    admin_client.force_login(admin_user)
    admin_resp = admin_client.get('/admin/')
    print(f"Admin page status: {admin_resp.status_code}")
    
    content_lower = admin_resp.content.decode().lower()
    if 'site-name' in content_lower or 'django' in content_lower:
        print("Admin page: Looks like Django admin interface")
    else:
        print("Admin page: May not look like admin interface")
else:
    print("No admin user found!")

print()
print("=== 4. THUMBNAIL TEST ===")

from store.models import ProductImage
broken_images = 0
working_images = 0

for product in Product.objects.filter(is_active=True).distinct()[:10]:
    img = product.images.first() if hasattr(product, 'images') else None
    if img and img.src_url:
        if img.src_url.startswith('http'):
            print(f"External image: {product.name[:40]}")
            print(f"  URL: {img.src_url[:80]}...")
            broken_images += 1
        else:
            working_images += 1

print(f"External images (potential issues): {broken_images}")
print(f"Local/static images: {working_images}")

print()
print("=== 5. FILES TO CLEANUP ===")
files_to_check = ['db.sqlite3', 'populate_db.py', 'steamspy.log', 'seed.log', 'sweep.log', 'staticfiles']
for f in files_to_check:
    exists = os.path.exists(f) if not f.endswith('/') else os.path.isdir(f)
    print(f"{f}: {'EXISTS' if exists else 'not found'}")

print()
print("=== DONE ===")



from django.test import Client
from django.contrib.auth.models import User
from store.models import Product, Cart, Order
import re

product = Product.objects.filter(is_digital=True, is_active=True).first()
if not product:
    print("NO DIGITAL PRODUCTS FOUND")
    exit()

print(f"Product: {product.name} (id={product.id})")
print(f"Price: Rs. {product.current_price}")
print(f"Digital: {product.is_digital}")
print()

user, _ = User.objects.get_or_create(
    username='checkout_test_user',
    defaults={'email': 'checkout@test.com', 'first_name': 'Test', 'last_name': 'User'}
)
user.set_password('testpass123')
user.save()

client = Client()
client.force_login(user)

resp = client.post(f'/cart/add/{product.id}/', {'quantity': 1})
print(f"Add to cart: {resp.status_code}")
cart = Cart.objects.filter(user=user).first()
if cart:
    print(f"Cart total: Rs. {cart.total}, items: {cart.items_count}")
print()

resp = client.get('/checkout/')
print(f"Checkout GET: {resp.status_code}")
body = resp.content.decode()

pm_section = re.search(r'name="payment_method".*?</select>', body, re.DOTALL)
if pm_section:
    print("Payment methods found in form:")
    options = re.findall(r'<option[^>]*value="([^"]+)"', pm_section.group())
    for opt in options:
        print(f"  - {opt}")
else:
    pm_radios = re.findall(r'name="payment_method".*?value="([^"]+)"', body)
    if pm_radios:
        print(f"Payment methods (radio): {pm_radios}")
    else:
        print("WARNING: No payment_method field found in form!")
        if 'payment_method' in body:
            idx = body.index('payment_method')
            print(f"Context: ...{body[max(0,idx-100):idx+200]}...")
print()

test_data = {
    'csrfmiddlewaretoken': 'test',
    'billing_full_name': 'Test User',
    'billing_phone': '9800000000',
    'billing_email': 'test@example.com',
    'billing_address_line_1': '123 Test St',
    'billing_city': 'Kathmandu',
    'billing_state': 'Bagmati',
    'billing_postal_code': '44600',
    'billing_country': 'Nepal',
    'payment_method': 'nay_bank',
    'terms_accepted': 'on',
}

payment_methods = ['esewa', 'nay_bank']
for pm in payment_methods:
    test_data['payment_method'] = pm
    resp = client.get('/checkout/')
    body = resp.content.decode()
    csrf_match = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', body)
    if csrf_match:
        test_data['csrfmiddlewaretoken'] = csrf_match.group(1)

    resp = client.post('/checkout/', test_data)
    print(f"POST {pm.upper():15s}: status={resp.status_code}", end="")
    if hasattr(resp, 'url'):
        print(f", redirect={resp.url}")
    else:
        body = resp.content.decode()
        if 'errorlist' in body or 'This field is required' in body:
            errors = re.findall(r'<ul[^>]*class="[^"]*errorlist[^"]*".*?</ul>', body, re.DOTALL)
            if errors:
                print(f", ERRORS: {len(errors)} field(s)")
                for e in errors[:2]:
                    field = re.search(r'for="([^"]+)"', e)
                    if field:
                        print(f"    - {field.group(1)}")
            else:
                print(f", FORM RE-RENDERED (no redirect)")
                title_match = re.search(r'<title>([^<]+)</title>', body)
                if title_match:
                    print(f"    Title: {title_match.group(1)}")
        else:
            print(f", no redirect, no errors visible")
            title_match = re.search(r'<title>([^<]+)</title>', body)
            if title_match:
                print(f"    Title: {title_match.group(1)}")
    print()

orders = Order.objects.filter(user=user)
print(f"Orders created: {orders.count()}")
for o in orders:
    print(f"  {o.order_number}: status={o.status}, payment={o.payment_method}")

Order.objects.filter(user=user).delete()
Cart.objects.filter(user=user).delete()
user.delete()
print("\nDone")
