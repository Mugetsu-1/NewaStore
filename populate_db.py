"""
Seed the Newa Store database with a real game catalog from live APIs.

Sources (no API keys needed):
    1. CheapShark enumeration sweep — band x sort x store combos (the `page`
       param is broken on their /deals endpoint, so we enumerate by slicing
       price bands per store instead). Yields every active deal with real
       prices, Metacritic scores and Steam ratings.
    2. Steam appdetails — genres, real descriptions, developers, publishers,
       release dates and screenshots for the top games (featured set).

Prices are real USD store prices converted to NPR at USD_TO_NPR. Images are
hotlinked from the Steam CDN (deterministic capsule URLs) — no downloads.

Usage:
    python populate_db.py            # seed only if catalog is empty
    python populate_db.py --reset    # wipe store data first, then seed

For the full Steam catalog (~60-80k games) run the background importer:
    python manage.py import_steamspy

Environment variables:
    USD_TO_NPR        USD -> NPR conversion rate        (default: 135)
    SEED_MAX_REQUESTS max CheapShark requests in sweep  (default: 400)
    SEED_ENRICH       how many top games to enrich      (default: 150)
"""
import os
import sys
import shutil
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'newastore.settings')
django.setup()

from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.utils import timezone

from store.models import SiteSettings, Coupon, ShippingMethod, Order, OrderItem, \
    OrderStatusHistory, Address, NewsletterSubscriber, ContactMessage, Wishlist, \
    Product, ProductImage, Category, Tag
from store.importers import (wait_for_cheapshark, sweep_deals, upsert_games,
                             enrich_with_steam)

MAX_REQUESTS = int(os.environ.get('SEED_MAX_REQUESTS', '400'))
ENRICH_LIMIT = int(os.environ.get('SEED_ENRICH', '150'))
FEATURED_COUNT = 12

COUPONS = [
    ("WELCOME10", "Welcome 10% Off", "percentage", 10, 0, 500, "New customers get 10% off"),
    ("SAVE20", "Save 20%", "percentage", 20, 15000, 3000, "20% off orders over Rs. 15,000"),
    ("FLAT500", "Flat Rs. 500 Off", "fixed", 500, 5000, None, "Rs. 500 off orders over Rs. 5,000"),
    ("FREESHIP", "Free Shipping", "free_shipping", 0, 0, None, "Free shipping on any order"),
]


def _pick_featured_and_enrich():
    """Mark the top Metacritic games as featured, then Steam-enrich the best."""
    top = (Product.objects.filter(metacritic_score__isnull=False)
           .order_by('-metacritic_score', '-published_at')
           .values_list('id', flat=True)[:FEATURED_COUNT])
    if top:
        Product.objects.filter(is_featured=True).update(is_featured=False)
        Product.objects.filter(id__in=list(top)).update(is_featured=True)
        print(f"  {len(top)} featured games selected.")

    if ENRICH_LIMIT > 0:
        enrich_targets = (Product.objects
                          .filter(steam_app_id__isnull=False, steam_enriched=False)
                          .order_by('-is_featured', '-metacritic_score', '-published_at')
                          [:ENRICH_LIMIT])
        print(f"Steam-enriching top {len(enrich_targets)} games "
              "(genres, descriptions, screenshots)...")
        enriched = enrich_with_steam(enrich_targets, limit=ENRICH_LIMIT)
        print(f"  {enriched} games enriched.")


def wipe():
    print("Wiping existing store data...")
    OrderItem.objects.all().delete()
    OrderStatusHistory.objects.all().delete()
    Order.objects.all().delete()
    ProductImage.objects.all().delete()
    Product.objects.all().delete()
    Category.objects.all().delete()
    Tag.objects.all().delete()
    Coupon.objects.all().delete()
    ShippingMethod.objects.all().delete()
    Address.objects.all().delete()
    NewsletterSubscriber.objects.all().delete()
    ContactMessage.objects.all().delete()
    Wishlist.objects.all().delete()
    # remove orphaned local images from previous seeds (imports now hotlink)
    if default_storage.exists('products'):
        shutil.rmtree(os.path.join(default_storage.location, 'products'),
                      ignore_errors=True)
        print("  (cleared local media/products)")


def seed(reset=False, sweep_only=False):
    if reset:
        wipe()

    if sweep_only:
        # Refresh prices/deals from CheapShark without touching anything else.
        print("Waiting for CheapShark API availability...")
        if not wait_for_cheapshark():
            print("CheapShark is still rate-limiting us. Try again later with:\n"
                  "    python populate_db.py --sweep-only")
            sys.exit(1)

        def progress(requests_used, unique, added):
            if requests_used % 25 == 0 or requests_used == 1:
                print(f"  sweep: {requests_used} requests, {unique} unique games"
                      f" (+{added} last batch)")

        print(f"Sweeping CheapShark (up to {MAX_REQUESTS} requests)...")
        games = sweep_deals(max_requests=MAX_REQUESTS, on_progress=progress)
        if not games:
            print("No games fetched — check your internet connection. Aborting.")
            sys.exit(1)
        print(f"  {len(games)} unique games collected. Importing (upsert)...")
        created, updated = upsert_games(games, data_source='cheapshark')
        print(f"  imported {created} new games, updated {updated} existing.")
        _pick_featured_and_enrich()
        print("\nSweep complete!")
        return

    # --- Site settings (store configuration, not product data) ---
    s = SiteSettings.get_settings()
    s.site_name = "Newa Store"
    s.site_tagline = "Real games, real prices — digital keys delivered instantly."
    s.email = "support@newastore.com"
    s.phone = "+977-9800000000"
    s.address = "Durbar Marg, Kathmandu 44600, Nepal"
    s.meta_title = "Newa Store — Digital Game Keys"
    s.meta_description = ("Shop digital PC game keys for Steam, GOG and more — live price "
                          "data from international stores, delivered instantly.")
    s.meta_keywords = "games, pc games, steam keys, gog, digital store, nepali online store"
    s.free_shipping_threshold = Decimal("0")
    s.tax_rate = Decimal("13")
    s.save()
    print("Site settings ready.")

    # --- Shipping methods (needed for checkout; digital goods ship free) ---
    ShippingMethod.objects.get_or_create(name="Digital Delivery", defaults=dict(
        description="Game keys delivered by email — instantly", price=Decimal("0"),
        estimated_days_min=0, estimated_days_max=0, sort_order=0))
    ShippingMethod.objects.get_or_create(name="Standard Delivery", defaults=dict(
        description="Delivery in 3-5 business days", price=Decimal("100"),
        estimated_days_min=3, estimated_days_max=5, sort_order=1,
        free_shipping_threshold=Decimal("5000")))
    ShippingMethod.objects.get_or_create(name="Express Delivery", defaults=dict(
        description="Delivery in 1-2 business days", price=Decimal("250"),
        estimated_days_min=1, estimated_days_max=2, sort_order=2))
    ShippingMethod.objects.get_or_create(name="Pickup Point", defaults=dict(
        description="Collect from nearest pickup point", price=Decimal("60"),
        estimated_days_min=2, estimated_days_max=4, sort_order=3))
    print("Shipping methods ready.")

    # --- Catalog: live CheapShark enumeration sweep ---
    if Product.objects.filter(is_active=True).exists():
        print("Products already exist — skipping catalog fetch (use --reset to re-seed).")
    else:
        print("Waiting for CheapShark API availability...")
        if not wait_for_cheapshark():
            print("CheapShark is still rate-limiting us. Try again later with:\n"
                  "    python populate_db.py --reset")
            sys.exit(1)

        def progress(requests_used, unique, added):
            if requests_used % 25 == 0 or requests_used == 1:
                print(f"  sweep: {requests_used} requests, {unique} unique games"
                      f" (+{added} last batch)")

        print(f"Sweeping CheapShark (up to {MAX_REQUESTS} requests)...")
        games = sweep_deals(max_requests=MAX_REQUESTS, on_progress=progress)
        if not games:
            print("No games fetched — check your internet connection. Aborting.")
            sys.exit(1)
        print(f"  {len(games)} unique games collected. Importing...")

        created, updated = upsert_games(games, data_source='cheapshark')
        print(f"  imported {created} games ({updated} updated).")

        _pick_featured_and_enrich()

    # --- Coupons (store configuration) ---
    from datetime import timedelta
    now = timezone.now()
    for code, name, dtype, value, min_amt, max_disc, desc in COUPONS:
        Coupon.objects.get_or_create(code=code, defaults=dict(
            name=name, description=desc, discount_type=dtype,
            discount_value=Decimal(str(value)), minimum_amount=Decimal(str(min_amt)),
            maximum_discount=Decimal(str(max_disc)) if max_disc else None,
            valid_from=now - timedelta(days=1), valid_until=now + timedelta(days=365),
            is_active=True, usage_limit_per_user=1,
        ))
    print(f"{len(COUPONS)} coupons ready.")

    # --- Users (for the demo and smoke tests) ---
    if not User.objects.filter(username="admin").exists():
        User.objects.create_superuser("admin", "admin@newastore.com", "admin123",
                                      first_name="Store", last_name="Admin")
        print("Superuser created -> admin / admin123")
    for uname, fname, lname in [("demo", "Demo", "Customer")]:
        u, created_ = User.objects.get_or_create(username=uname, defaults=dict(
            email=f"{uname}@example.com", first_name=fname, last_name=lname))
        if created_:
            u.set_password("demo1234")
            u.save()
        Wishlist.objects.get_or_create(user=u)
    print("Demo user ready -> demo / demo1234")

    print("\nSeed complete!")
    print("Admin:    http://127.0.0.1:8000/admin/  (admin / admin123)")
    print("Customer: demo / demo1234")
    print("Full Steam catalog:  python manage.py import_steamspy")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv, sweep_only="--sweep-only" in sys.argv)
