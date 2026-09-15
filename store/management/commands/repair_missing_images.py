"""Create missing product thumbnails from the Steam CDN.

Products imported without an image row (or whose stored URL was blanked during
an audit) show the local placeholder. For any of them that still carry a
``steam_app_id`` we can rebuild a working capsule/header URL, so this command
probes the usual Steam asset patterns and attaches the first one that loads.

Usage:

    python manage.py repair_missing_images --limit 500
    python manage.py repair_missing_images --workers 24
"""
import concurrent.futures as futures
import time

import requests

from django.core.management.base import BaseCommand

from store.models import Product, ProductImage

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

HOSTS = (
    "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/",
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/",
)
FILES = ("capsule_616x353.jpg", "header.jpg", "library_600x900.jpg", "capsule_231x87.jpg")


def check(url):
    try:
        r = requests.get(url, headers=UA, timeout=8, stream=True, allow_redirects=True)
        code = r.status_code
        r.close()
        return code in (200, 206)
    except requests.RequestException:
        return False


def find_image(appid):
    for host in HOSTS:
        for name in FILES:
            candidate = host.format(appid=appid) + name
            if check(candidate):
                return candidate
    return ""


class Command(BaseCommand):
    help = "Attach a working Steam CDN thumbnail to products that have none."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Max products to repair (0 = all).")
        parser.add_argument("--workers", type=int, default=16, help="Concurrent probes.")

    def handle(self, *args, **options):
        limit = options["limit"]
        workers = max(1, options["workers"])

        qs = (
            Product.objects.filter(images__isnull=True, steam_app_id__isnull=False)
            .order_by("id")
            .distinct()
        )
        if limit:
            qs = qs[:limit]
        targets = list(qs.values_list("id", "steam_app_id", "name"))
        total = len(targets)
        self.stdout.write(f"Repairing {total} product(s) without images, {workers} workers...")
        if not total:
            return

        recovered = missing = 0
        started = time.time()
        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(find_image, appid): (pid, name)
                       for pid, appid, name in targets}
            done = 0
            for fut in futures.as_completed(pending):
                pid, name = pending[fut]
                done += 1
                try:
                    url = fut.result()
                except Exception as exc:  # noqa: BLE001 - report and continue
                    self.stderr.write(f"  probe error product={pid}: {exc}")
                    continue
                if url:
                    ProductImage.objects.create(
                        product_id=pid, external_url=url,
                        alt_text=name[:200], is_primary=True, sort_order=0,
                    )
                    recovered += 1
                else:
                    missing += 1
                if done % 100 == 0:
                    self.stdout.write(f"  {done}/{total} processed (recovered={recovered})")

        self.stdout.write(self.style.SUCCESS(
            f"Done: recovered={recovered} no_art_found={missing} "
            f"in {time.time() - started:.0f}s"))