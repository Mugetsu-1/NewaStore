import os, sys, subprocess, urllib.request, re, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'newastore.settings')
import django
django.setup()

from django.conf import settings
from store.models import Product, ProductImage
from django.db import connection

print("=" * 70)
print("NEWA STORE — STATUS DUMP")
print("=" * 70)

# 1. Artwork storage
print("\n[1] ARTWORK STORAGE")
print(f"  ARTWORK_STORAGE: {getattr(settings, 'ARTWORK_STORAGE', 'NOT SET')}")
print(f"  FileBasedStorage.base_url: {settings.FILE_BASED_STORAGE.base_url if hasattr(settings, 'FILE_BASED_STORAGE') else 'n/a'}")
if os.path.exists(settings.ARTWORK_STORAGE.local_path):
    print(f"  local_path: {settings.ARTWORK_STORAGE.local_path}")
    print(f"  exists: YES, size={len(os.listdir(settings.ARTWORK_STORAGE.local_path))} files")
else:
    print(f"  local_path: {settings.ARTWORK_STORAGE.local_path}")
    print(f"  exists: NO — materialization NOT done")

# 2. Products with/without artwork
total = Product.objects.filter(is_active=True).count()
with_art = Product.objects.filter(is_active=True, images__isnull=False).distinct().count()
without_art = total - with_art
print(f"\n[2] PRODUCTS")
print(f"  Total active: {total}")
print(f"  With artwork (Django relation): {with_art}")
print(f"  Without artwork: {without_art}")

# 3. Sample products - check what image URL they'd get
print(f"\n[3] SAMPLE PRODUCT IMAGE URLS (first 10 with artwork + 5 without)")
samples_with = list(Product.objects.filter(is_active=True, images__isnull=False).distinct()[:10])
samples_without = list(Product.objects.filter(is_active=True, images__isnull=True)[:5])

for p in samples_with[:5]:
    img = p.images.first()
    url = p.primary_image_url
    local = p.local_image_path
    local_exists = os.path.exists(local) if local else False
    print(f"  [{p.id}] {p.name[:40]:42s} django={bool(img)} url={url[:50]} local_exists={local_exists}")

for p in samples_without[:5]:
    url = p.primary_image_url
    local = p.local_image_path
    local_exists = os.path.exists(local) if local else False
    print(f"  [{p.id}] {p.name[:40]:42s} django=NO  url={url[:50]} local_exists={local_exists}")

# 4. What MaterializedImageStorage returns
print(f"\n[4] WHAT THE WEBSITE SERVES (MaterializedImageStorage.url())")
for p in samples_with[:3]:
    img = p.images.first()
    if img:
        try:
            url = img.storage.url(img.name)
            print(f"  [{p.id}] {p.name[:40]:42s} storage.url={url}")
        except Exception as e:
            print(f"  [{p.id}] {p.name[:40]:42s} storage.url ERROR: {e}")

for p in samples_without[:3]:
    try:
        url = p.productimage_set.first().storage.url(p.productimage_set.first().name) if p.productimage_set.first() else None
        print(f"  [{p.id}] {p.name[:40]:42s} no image -> placeholder")
    except Exception as e:
        print(f"  [{p.id}] {p.name[:40]:42s} ERROR: {e}")

# 5. Tests
print(f"\n[5] RUNNING TESTS...")
result = subprocess.run(
    [sys.executable, 'manage.py', 'test', 'store', '--verbosity=0'],
    capture_output=True, text=True, cwd=os.getcwd()
)
print(result.stdout[-500:] if result.stdout else '')
print(result.stderr[-500:] if result.stderr else '')
print(f"  exit code: {result.returncode}")

print("\n" + "=" * 70)
print("THUMBNAIL PROBLEM SUMMARY")
print("=" * 70)
print("""
THE PROBLEM:
  81,270 products imported from SteamSpy/CheapShark have NO local images.
  Steam CDN URLs (store.steampowered.com) are hotlinked directly, but:
    - Steam blocks hotlinking (403/embedded player)
    - URLs expire/change over time ("asset rot")
    - First ~500 products on shop pages show only placeholders

WHAT WAS BUILT:
  - ARTWORK_STORAGE: custom Django storage that serves from local/static/artwork/
  - ProductImage model: stores filename only (NOT the remote URL)
  - Resolvers: Steam API, CheapShark API, Twitch IGDB for finding image URLs
  - MaterializedImageStorage: returns local path if cached, else generates placeholder SVG
  - materialize_images command: downloads → converts to WebP → writes to local storage

WHAT REMAINS:
  - The materialize_images command needs to be RUN to actually download images
  - Until run, products serve placeholders (SVG) — this is WHY thumbnails don't load
  - After materialization, ProductImage.storage.url() returns local file URLs
  - The website needs to reference these local URLs, not Steam CDN URLs

THE OPTIMAL FIX (from your proposal):
  1. Run materialize_images to download + WebP-convert all 81k images locally
  2. Ensure Product.image uses MaterializedImageStorage so it serves local files
  3. Optionally add Cloudflare R2/S3 + CDN for production asset hosting
  4. Use async Celery workers for the download pipeline (81k images = hours)
  5. Sort shop pages by has_artwork first to show captioned products above placeholders
""")
