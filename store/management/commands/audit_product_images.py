"""Probe hotlinked product images; recover 404s via Steam CDN alternates.

Steam removes some per-app assets over time, so a stored capsule URL can 404
while header.jpg / library art still exists. This command checks every stored
external_url and:

  - keeps it when it loads (HTTP 200/206; 403 treated as OK - bot-blocked but
    usually fine in a real browser),
  - swaps in the first working alternate pattern when the stored one is gone,
  - blanks it when nothing works, so templates fall back to the local
    placeholder instead of showing a broken-image icon.

Resumable and throttled:

    python manage.py audit_product_images --limit 2000 --workers 24
    python manage.py audit_product_images --after-id 12345   # resume
"""
import concurrent.futures as futures
import time

import requests

from django.core.management.base import BaseCommand

from store.models import ProductImage

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://store.steampowered.com/",
}

HOSTS = (
    "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/",
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/",
)
FILES = ("capsule_616x353.jpg", "header.jpg", "library_600x900.jpg", "capsule_231x87.jpg")


def appid_from_url(url):
    try:
        return url.split("/apps/")[1].split("/")[0]
    except (IndexError, AttributeError):
        return ""


def check(url):
    try:
        r = requests.get(url, headers=UA, timeout=8, stream=True, allow_redirects=True)
        code = r.status_code
        r.close()
        return code in (200, 206, 403)
    except requests.RequestException:
        return False


def audit_image(img):
    """Return (img.id, new_url) where new_url None = keep, str = update."""
    url = img.external_url
    if check(url):
        return (img.id, None)
    appid = appid_from_url(url)
    if not appid:
        return (img.id, "")
    for host in HOSTS:
        for name in FILES:
            candidate = host.format(appid=appid) + name
            if candidate == url:
                continue
            if check(candidate):
                return (img.id, candidate)
    return (img.id, "")


class Command(BaseCommand):
    help = "Probe hotlinked product images; recover 404s via Steam CDN alternates."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Max images to audit this run (0 = all).")
        parser.add_argument("--workers", type=int, default=16, help="Concurrent probes.")
        parser.add_argument("--after-id", type=int, default=0, help="Resume: audit ids greater than this.")

    def handle(self, *args, **options):
        limit = options["limit"]
        workers = max(1, options["workers"])
        after_id = options["after_id"]

        qs = ProductImage.objects.exclude(external_url="").filter(id__gt=after_id).order_by("id")
        if limit:
            qs = qs[:limit]
        imgs = list(qs.only("id", "external_url"))
        total = len(imgs)
        self.stdout.write(f"Auditing {total} image URL(s) with {workers} workers...")
        if not total:
            return

        checked = ok = recovered = blanked = failed = 0
        last_id = 0
        updates = []

        def flush(chunk):
            nonlocal recovered, blanked
            for img_id, new_url in chunk:
                ProductImage.objects.filter(pk=img_id).update(external_url=new_url)
                if new_url:
                    recovered += 1
                else:
                    blanked += 1

        started = time.time()
        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(audit_image, img): img.id for img in imgs}
            for fut in futures.as_completed(pending):
                img_id = pending[fut]
                last_id = max(last_id, img_id)
                try:
                    _, new_url = fut.result()
                except Exception as exc:
                    failed += 1
                    self.stderr.write(f"  probe error id={img_id}: {exc}")
                    continue
                checked += 1
                if new_url is None:
                    ok += 1
                else:
                    updates.append((img_id, new_url))
                if checked % 250 == 0:
                    rate = checked / max(time.time() - started, 0.001)
                    self.stdout.write(
                        f"  {checked}/{total} probed ({rate:.0f}/s) ok={ok} "
                        f"recovered={recovered} blanked={blanked} failed={failed}")
                if len(updates) >= 500:
                    flush(updates)
                    updates = []
        flush(updates)

        self.stdout.write(self.style.SUCCESS(
            f"Done: checked={checked} ok={ok} recovered={recovered} "
            f"blanked={blanked} failed={failed} in {time.time() - started:.0f}s. "
            f"Resume with --after-id {last_id}"))
