"""Fill in artwork for every product currently showing a placeholder.

Targets products that either have no ProductImage row or whose stored URL is
blank, then recovers art from two sources:

  1. Steam CDN  - for products with a ``steam_app_id``: probe the usual
     capsule/header/library patterns and keep the first one that loads.
  2. CheapShark - for ``data_source='cheapshark'`` products whose SKU is
     ``CS-<gameID>``: ask CheapShark for the deal ``thumb`` and verify it
     actually downloads before storing it.

Both sources are verified with a real request, so the store never ends up with
a URL that renders as a broken image.

    python manage.py repair_missing_images                 # everything
    python manage.py repair_missing_images --limit 300     # capped run
    python manage.py repair_missing_images --workers 32
"""
import concurrent.futures as futures
import time

import requests

from django.core.management.base import BaseCommand
from django.db.models import Q

from store.models import Product, ProductImage

STEAM_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
CS_UA = {
    "User-Agent": "NewaStore/1.0 (portfolio demo; contact: newastore8@gmail.com)",
    "Accept": "application/json",
}

STEAM_HOSTS = (
    "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/",
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/",
)
STEAM_FILES = ("capsule_616x353.jpg", "header.jpg", "library_600x900.jpg",
               "capsule_231x87.jpg")
CS_API = "https://www.cheapshark.com/api/1.0/games?ids="
CS_PAGE_SIZE = 40


def check(url, headers=STEAM_UA):
    """True when the URL actually returns image bytes."""
    try:
        r = requests.get(url, headers=headers, timeout=12, stream=True,
                         allow_redirects=True)
        code = r.status_code
        ctype = r.headers.get("Content-Type", "")
        r.close()
        return code in (200, 206) and ctype.startswith("image")
    except requests.RequestException:
        return False


def find_steam_art(appid):
    for host in STEAM_HOSTS:
        for name in STEAM_FILES:
            candidate = host.format(appid=appid) + name
            if check(candidate):
                return candidate
    return ""


class Command(BaseCommand):
    help = "Attach verified artwork to products currently showing a placeholder."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0,
                            help="Max products to repair (0 = all).")
        parser.add_argument("--workers", type=int, default=16,
                            help="Concurrent image probes.")
        parser.add_argument("--skip-cheapshark", action="store_true",
                            help="Steam CDN only (useful when offline).")

    def _save(self, product, url, stats):
        """Attach artwork, reusing an existing blank row when present."""
        existing = (ProductImage.objects.filter(product=product, external_url="")
                    .order_by("id").first())
        if existing:
            existing.external_url = url
            existing.alt_text = existing.alt_text or product.name[:200]
            existing.save(update_fields=["external_url", "alt_text"])
        else:
            ProductImage.objects.create(
                product=product, external_url=url,
                alt_text=product.name[:200], is_primary=True, sort_order=0)
        stats["recovered"] += 1

    def handle(self, *args, **options):
        limit = options["limit"]
        workers = max(1, options["workers"])
        skip_cs = options["skip_cheapshark"]
        verbosity = options["verbosity"]
        started = time.time()

        missing = Product.objects.filter(
            Q(images__isnull=True) | Q(images__external_url="")).distinct()
        if limit:
            missing = missing[:limit]
        total = missing.count()
        if not total:
            self.stdout.write(self.style.SUCCESS("Nothing to repair - no placeholders."))
            return
        self.stdout.write(f"{total:,} product(s) currently without artwork.")

        stats = {"recovered": 0, "steam_miss": 0, "cs_miss": 0, "failed": 0}

        steam_targets = list(
            missing.filter(steam_app_id__isnull=False)
            .values_list("id", "steam_app_id"))
        cs_products = list(
            missing.filter(steam_app_id__isnull=True, data_source="cheapshark",
                           sku__startswith="CS-")
            .values_list("id", "sku"))
        other = missing.filter(steam_app_id__isnull=True).exclude(
            data_source="cheapshark", sku__startswith="CS-").count()
        stats["cs_miss"] += other  # no recovery source for these

        self.stdout.write(f"  steam-appid products : {len(steam_targets):,}")
        self.stdout.write(f"  cheapshark products  : {len(cs_products):,}"
                          + ("  (skipped)" if skip_cs else ""))

        # ---- 1. Steam CDN -------------------------------------------------
        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(find_steam_art, appid): pid
                       for pid, appid in steam_targets}
            done = 0
            for fut in futures.as_completed(pending):
                pid = pending[fut]
                done += 1
                try:
                    url = fut.result()
                except Exception as exc:  # noqa: BLE001
                    stats["failed"] += 1
                    if verbosity >= 2:
                        self.stderr.write(f"  steam probe error id={pid}: {exc}")
                    continue
                if url:
                    self._save(Product.objects.get(pk=pid), url, stats)
                else:
                    stats["steam_miss"] += 1
                if done % 100 == 0:
                    self.stdout.write(f"  steam {done}/{len(steam_targets)} "
                                      f"(recovered={stats['recovered']:,})")

        # ---- 2. CheapShark thumbs (batched) --------------------------------
        if cs_products and not skip_cs:
            self.stdout.write(f"  querying CheapShark for {len(cs_products):,} thumb(s)...")
            by_sku = {sku: pid for pid, sku in cs_products}
            game_ids = [sku[3:] for _pid, sku in cs_products]
            cs_thumbs = {}
            for i in range(0, len(game_ids), CS_PAGE_SIZE):
                chunk = game_ids[i:i + CS_PAGE_SIZE]
                try:
                    r = requests.get(CS_API + ",".join(chunk), headers=CS_UA, timeout=25)
                    if r.status_code == 200:
                        for gid, obj in r.json().items():
                            thumb = (obj.get("info") or {}).get("thumb") or ""
                            if thumb:
                                cs_thumbs[f"CS-{gid}"] = thumb
                    else:
                        if verbosity >= 2:
                            self.stderr.write(f"  cheapshark HTTP {r.status_code} at batch {i}")
                except Exception as exc:  # noqa: BLE001
                    if verbosity >= 2:
                        self.stderr.write(f"  cheapshark error at batch {i}: {exc}")
                time.sleep(1.2)  # stay polite with the API

            self.stdout.write(
                f"  cheapshark returned {len(cs_thumbs):,} thumb(s); verifying...")

            def verify_cs(sku):
                url = cs_thumbs.get(sku, "")
                return sku, (url if url and check(url) else "")

            with futures.ThreadPoolExecutor(max_workers=workers) as pool:
                for sku, url in pool.map(verify_cs, list(cs_thumbs)):
                    pid = by_sku.get(sku)
                    if not pid:
                        continue
                    if url:
                        self._save(Product.objects.get(pk=pid), url, stats)
                    else:
                        stats["cs_miss"] += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done in {time.time() - started:.0f}s: recovered={stats['recovered']:,} "
            f"steam_no_art={stats['steam_miss']:,} cheapshark_no_art={stats['cs_miss']:,} "
            f"errors={stats['failed']:,}"))
        remaining = Product.objects.filter(
            Q(images__isnull=True) | Q(images__external_url="")).distinct().count()
        self.stdout.write(f"Products still without artwork: {remaining:,}")