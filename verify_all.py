"""End-to-end verification against the LIVE Postgres database.

Covers: image coverage, every core route, the simulated wallet checkout,
Nay Bank transfer, admin access, and SMTP configuration.
Creates a throwaway user + orders and cleans them up afterwards.
"""
import os
import sys

# Use the in-memory email backend for this run: it exercises the real send path
# without firing dozens of live emails at Gmail.
os.environ["DJANGO_EMAIL_BACKEND"] = "django.core.mail.backends.locmem.EmailBackend"

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "newastore.settings")
import django  # noqa: E402

django.setup()

from django.core import mail  # noqa: E402

from django.conf import settings  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.test import Client  # noqa: E402

from store.models import (  # noqa: E402
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
from dotenv import dotenv_values  # noqa: E402

env = dotenv_values(".env")
check("PostgreSQL in use", "postgresql" in settings.DATABASES["default"]["ENGINE"])
check("no sqlite engine configured", "sqlite3" not in settings_src.lower())
check("no db.sqlite3 file on disk", not os.path.exists("db.sqlite3"))
check("SMTP backend configured in .env",
      "smtp" in (env.get("DJANGO_EMAIL_BACKEND") or ""),
      env.get("DJANGO_EMAIL_BACKEND") or "missing")
check("Gmail app password present in .env", bool(env.get("EMAIL_HOST_PASSWORD")))
check("eSewa secret not hardcoded in settings.py", "8gBm" not in settings_src)
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
    except Exception as exc:  # noqa: BLE001
        check(f"{label} ({url})", False, f"{type(exc).__name__}: {exc}")

api_checks = [
    # (label, url, validator)
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
    except Exception as exc:  # noqa: BLE001
        check(f"{label} ({url})", False, f"{type(exc).__name__}: {exc}")

print()
print("=" * 68)
print("4. CHECKOUT — SIMULATED eSEWA / KHALTI + NAY BANK")
print("=" * 68)
SiteSettings.get_settings()

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
        "billing_email": "verify@example.com", "billing_address_line_1": "Test St",
        "billing_address_line_2": "", "billing_city": "Kathmandu",
        "billing_state": "Bagmati", "billing_postal_code": "44600",
        "billing_country": "Nepal", "payment_method": method,
        "order_notes": "", "terms_accepted": "on",
    })


buyer = Client()
check("login works", buyer.login(username=USERNAME, password="verify12345") is True)

buyer.post(f"/cart/add/{checkout_product.id}/", {"quantity": 1})
page = buyer.get("/checkout/").content.decode()
check("checkout page renders", "Place Order" in page)
for value, label in [("esewa", "eSewa (Simulated)"), ("khalti", "Khalti (Simulated)"),
                     ("nay_bank", "Nay Bank Transfer")]:
    check(f"checkout offers {value}", f'value="{value}"' in page and label in page)

# --- eSewa simulated page
resp = place_order(buyer, "esewa")
order = Order.objects.filter(user=user).order_by("-id").first()
body = resp.content.decode() if resp.status_code == 200 else ""
check("eSewa returns a local page (no external redirect)",
      resp.status_code == 200, f"HTTP {resp.status_code}")
check("eSewa page branded + marked simulated",
      "Simulated payment" in body and "eSewa" in body)
check("eSewa page contacts no external host",
      "esewa.com.np" not in body and "rc-epay" not in body)
check("order unpaid before confirmation", bool(order) and not order.is_paid)

resp = buyer.post(f"/payment/simulate/{order.order_number}/esewa/", {"outcome": "success"})
order.refresh_from_db()
check("simulate success redirects to order_success",
      resp.status_code == 302 and f"/order/success/{order.order_number}/" in resp.url,
      resp.get("Location", ""))
check("order marked paid", order.is_paid, order.payment_status)
check("order confirmed", order.status == "confirmed")
check("SIM transaction id stored", order.payment_transaction_id.startswith("SIM-"),
      order.payment_transaction_id)

# --- simulate failure
place_order(buyer, "khalti")
order2 = Order.objects.filter(user=user).order_by("-id").first()
resp = buyer.post(f"/payment/simulate/{order2.order_number}/khalti/", {"outcome": "failure"})
order2.refresh_from_db()
check("simulate failure redirects to payment_failed",
      resp.status_code == 302 and "/payment/failed/" in resp.url)
check("declined order marked failed", order2.payment_status == "failed")

r = buyer.get(f"/payment/simulate/{order2.order_number}/stripe/")
check("unknown gateway 404s", r.status_code == 404, f"HTTP {r.status_code}")

# --- Nay Bank
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

# Staff should see admin affordances on the storefront; shoppers must not.
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