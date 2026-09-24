"""End-to-end verification against the LIVE Postgres database.

Covers: image coverage, every core route, the real-gateway checkout
(eSewa ePay v2 signature verify + forged-callback rejection), Nay Bank
transfer, admin access, and SMTP configuration.
Creates a throwaway user + orders and cleans them up afterwards.
"""
import os
import sys

os.environ["DJANGO_EMAIL_BACKEND"] = "django.core.mail.backends.locmem.EmailBackend"

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "newastore.settings")
import django

django.setup()

from django.core import mail

from django.conf import settings

if "testserver" not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

from django.contrib.auth.models import User
from django.test import Client

from store.models import (
    Cart, Order, Product, ProductImage, SiteSettings,
)

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -> {detail}" if detail else ""))


print("=" * 68)
print("1. IMAGE COVERAGE")
print("=" * 68)
total = Product.objects.count()
with_img = Product.objects.filter(images__isnull=False).distinct().count()
pct = with_img * 100.0 / total
print(f"  Products: {total:,}")
print(f"  With a thumbnail row: {with_img:,} ({pct:.1f}%)")
print(f"  Placeholder only: {total - with_img:,} ({100 - pct:.1f}%)")
blank = ProductImage.objects.filter(external_url="").count()
print(f"  Blank external_url rows: {blank:,}")
check("image coverage >= 97%", pct >= 97.0, f"{pct:.1f}%")
appid_pct = Product.objects.filter(steam_app_id__isnull=False).count() * 100.0 / total
check("most products carry a steam_app_id", appid_pct > 90, f"{appid_pct:.1f}%")

print()
print("=" * 68)
print("2. CONFIGURATION")
print("=" * 68)
print(f"  DB engine : {settings.DATABASES['default']['ENGINE']}")
print(f"  DB port   : {settings.DATABASES['default']['PORT']}")
print(f"  Email     : {settings.EMAIL_BACKEND}")
print(f"  From      : {settings.DEFAULT_FROM_EMAIL}")
settings_src = open("newastore/settings.py", encoding="utf-8").read()
views_src = open("store/views.py", encoding="utf-8").read()
from dotenv import dotenv_values

env = dotenv_values(".env")
check("PostgreSQL in use", "postgresql" in settings.DATABASES["default"]["ENGINE"])
check("no sqlite engine configured", "sqlite3" not in settings_src.lower())
check("no db.sqlite3 file on disk", not os.path.exists("db.sqlite3"))
check("SMTP backend configured in .env",
      "smtp" in (env.get("DJANGO_EMAIL_BACKEND") or ""),
      env.get("DJANGO_EMAIL_BACKEND") or "missing")
check("Gmail app password present in .env", bool(env.get("EMAIL_HOST_PASSWORD")))
check("eSewa secret read from environment in settings.py",
      "os.environ.get('ESEWA_SECRET_KEY'" in settings_src)
check("eSewa secret not hardcoded in views.py", "8gBm" not in views_src)
check("DJANGO_DB switch removed from .env", "DJANGO_DB" not in env)

print()
print("=" * 68)
print("3. CORE ROUTE SMOKE TEST (anonymous)")
print("=" * 68)
anon = Client()
product = Product.objects.filter(is_active=True).exclude(slug="").first()
routes = [
    ("home", "/"),
    ("shop", "/shop/"),
    ("search (query)", f"/search/?q={product.name[:12].replace(' ', '+')}"),
    ("search suggest", "/search/suggest/?q=the"),
    ("product detail", product.get_absolute_url()),
    ("quick view", f"/product/{product.slug}/quick-view/"),
    ("cart", "/cart/"),
    ("cart mini", "/cart/mini/"),
    ("login", "/login/"),
    ("register", "/register/"),
    ("about", "/about/"),
    ("contact", "/contact/"),
    ("faq", "/faq/"),
    ("privacy", "/privacy/"),
    ("terms", "/terms/"),
    ("refund policy", "/refund-policy/"),
    ("password reset", "/password-reset/"),
    ("sitemap", "/sitemap.xml"),
    ("robots", "/robots.txt"),
]
for label, url in routes:
    try:
        r = anon.get(url)
        check(f"{label} ({url})", r.status_code in (200, 301, 302), f"HTTP {r.status_code}")
    except Exception as exc:
        check(f"{label} ({url})", False, f"{type(exc).__name__}: {exc}")

api_checks = [
    ("api health", "/api/health/", lambda d: d.get("status") == "ok" and d.get("database") is True),
    ("api genres", "/api/genres/", lambda d: isinstance(d, list) and len(d) > 0
     and {"slug", "name", "product_count"} <= set(d[0])),
    ("api search", "/api/search/?q=the", lambda d: d.get("count", 0) > 0
     and isinstance(d.get("results"), list)),
    ("api browse", "/api/browse/", lambda d: "results" in d and "count" in d),
]
for label, url, validator in api_checks:
    try:
        r = anon.get(url)
        payload = r.json()
        check(f"{label} ({url})", r.status_code == 200 and validator(payload),
              f"HTTP {r.status_code}")
    except Exception as exc:
        check(f"{label} ({url})", False, f"{type(exc).__name__}: {exc}")

print()
print("=" * 68)
print("4. CHECKOUT — REAL eSEWA (ePay v2) SIGNATURE VERIFY + NAY BANK")
print("=" * 68)
SiteSettings.get_settings()

import base64
import json
from store.payments import _esewa_signature, is_esewa_configured

USERNAME = "verify_flow_user"
Order.objects.filter(user__username=USERNAME).delete()
User.objects.filter(username=USERNAME).delete()
Cart.objects.filter(user__username=USERNAME).delete()

user = User.objects.create_user(USERNAME, "verify@example.com", "verify12345")
user.first_name, user.last_name = "Verify", "Flow"
user.save()

checkout_product = Product.objects.filter(is_active=True).first() or product


def place_order(client, method):
    client.post(f"/cart/add/{checkout_product.id}/", {"quantity": 1})
    return client.post("/checkout/", {
        "billing_full_name": "Verify Flow", "billing_phone": "9800000000",
        "billing_email": "verify@gmail.com", "billing_address_line_1": "Test St",
        "billing_address_line_2": "", "billing_city": "Kathmandu",
        "billing_state": "Bagmati", "billing_postal_code": "44600",
        "billing_country": "Nepal", "payment_method": method,
        "order_notes": "", "terms_accepted": "on",
    })


def signed_esewa_data(order, status="COMPLETE", amount=None):
    """Build a base64 ?data= payload signed with the real eSewa test secret."""
    amount = f"{order.total:.2f}" if amount is None else amount
    fields = {
        "transaction_code": "000TESTCODE", "status": status,
        "total_amount": amount, "transaction_uuid": order.order_number,
        "product_code": settings.ESEWA_PRODUCT_CODE,
        "signed_field_names":
            "transaction_code,status,total_amount,transaction_uuid,product_code,signed_field_names",
    }
    names = fields["signed_field_names"].split(",")
    fields["signature"] = _esewa_signature(",".join(f"{n}={fields[n]}" for n in names))
    return base64.b64encode(json.dumps(fields).encode()).decode()


buyer = Client()
check("login works", buyer.login(username=USERNAME, password="verify12345") is True)

buyer.post(f"/cart/add/{checkout_product.id}/", {"quantity": 1})
page = buyer.get("/checkout/").content.decode()
check("checkout page renders", "Place Order" in page)
check("checkout offers eSewa", 'value="esewa"' in page and is_esewa_configured())
check("checkout offers Nay Bank Transfer",
      'value="nay_bank"' in page and "Nay Bank Transfer" in page)
check("checkout shows no 'Simulated' wording", "Simulated" not in page)

resp = place_order(buyer, "esewa")
order = Order.objects.filter(user=user).order_by("-id").first()
check("eSewa checkout redirects into the signed-form page",
      resp.status_code == 302, f"HTTP {resp.status_code}")
form_page = buyer.get(f"/payment/esewa/{order.order_number}/").content.decode()
check("eSewa form posts to the real gateway URL", settings.ESEWA_FORM_URL in form_page)
check("eSewa form carries a signature field", 'name="signature"' in form_page)
check("eSewa form carries the transaction id", 'name="transaction_uuid"' in form_page)
check("order unpaid before eSewa confirms", bool(order) and not order.is_paid)

resp = buyer.get("/esewa-verify/", {"data": signed_esewa_data(order)})
order.refresh_from_db()
check("valid signed eSewa callback redirects to order_success",
      resp.status_code == 302 and f"/order/success/{order.order_number}/" in resp.url,
      resp.get("Location", ""))
check("order marked paid on verified eSewa callback", order.is_paid, order.payment_status)
check("order confirmed", order.status == "confirmed")

paid_txn = order.payment_transaction_id
buyer.get("/esewa-verify/", {"data": signed_esewa_data(order)})
order.refresh_from_db()
check("verified eSewa callback is idempotent on a paid order",
      order.is_paid and order.payment_transaction_id == paid_txn, paid_txn)

place_order(buyer, "esewa")
order_f = Order.objects.filter(user=user).order_by("-id").first()
tampered = signed_esewa_data(order_f, amount=f"{order_f.total + 1:.2f}")
resp = buyer.get("/esewa-verify/", {"data": tampered})
order_f.refresh_from_db()
check("forged eSewa amount is rejected (order not paid)", not order_f.is_paid,
      order_f.payment_status)
check("forged eSewa callback redirects to payment_failed",
      resp.status_code == 302 and "/payment/failed/" in resp.url)

place_order(buyer, "nay_bank")
order3 = Order.objects.filter(user=user).order_by("-id").first()
bank_page = buyer.get(f"/order/success/{order3.order_number}/").content.decode()
check("nay_bank order stays pending",
      order3.payment_status == "pending" and order3.status == "pending")
check("bank page shows Nay Bank details",
      "Nay Bank" in bank_page and "0123456789012" in bank_page)
check("bank page shows the order reference", order3.order_number in bank_page)

for label, url in [("order history", "/orders/"),
                   ("order detail", f"/orders/{order.order_number}/"),
                   ("invoice", f"/orders/{order.order_number}/invoice/"),
                   ("profile", "/profile/"),
                   ("wishlist", "/wishlist/")]:
    r = buyer.get(url)
    check(f"{label} (logged in)", r.status_code == 200, f"HTTP {r.status_code}")
print()
print("=" * 68)
print("5. ADMIN ACCESS")
print("=" * 68)
admin_user = User.objects.filter(is_superuser=True).first()
check("superuser exists", admin_user is not None,
      admin_user.username if admin_user else "none")
if admin_user:
    ac = Client()
    ac.force_login(admin_user)
    r = ac.get("/admin/")
    check("admin dashboard reachable", r.status_code == 200, f"HTTP {r.status_code}")
    for label, url in [("products", "/admin/store/product/"),
                       ("orders", "/admin/store/order/"),
                       ("site settings", "/admin/store/sitesettings/"),
                       ("coupons", "/admin/store/coupon/")]:
        r = ac.get(url)
        check(f"admin {label}", r.status_code == 200, f"HTTP {r.status_code}")

anon_admin = anon.get("/admin/")
check("anonymous admin request redirects to login",
      anon_admin.status_code == 302 and "login" in anon_admin.url.lower(),
      f"HTTP {anon_admin.status_code}")

if admin_user:
    staff_home = ac.get("/").content.decode()
    anon_home = anon.get("/").content.decode()
    check("staff storefront shows Admin link", "Admin Dashboard" in staff_home
          and "Manage Orders" in staff_home)
    check("staff storefront shows STAFF badge", "staff-badge" in staff_home)
    check("anonymous storefront has no admin links",
          "Admin Dashboard" not in anon_home and "/admin/" not in anon_home)

buyer_home = buyer.get("/").content.decode()
check("normal shopper has no admin links",
      "Admin Dashboard" not in buyer_home and "staff-badge" not in buyer_home)

print()
print("=" * 68)
print("CLEANUP")
print("=" * 68)
Order.objects.filter(user=user).delete()
Cart.objects.filter(user=user).delete()
User.objects.filter(username=USERNAME).delete()
print("  removed verification user, its cart and orders")

print()
print("=" * 68)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
print("=" * 68)
if FAIL:
    print("\nFailures:")
    for name in FAIL:
        print(f"  - {name}")
    sys.exit(1)
print("All checks passed.")