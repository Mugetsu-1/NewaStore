"""Materialize card thumbnails: download each artwork once, store WebP locally.

This is the fetch-once pipeline. For every ProductImage that has a hotlinked
URL but no local thumbnail yet, the source image is downloaded once, converted
to a light WebP (default 480px wide, quality 82) and saved into MEDIA_ROOT via
Django's storage backend. From then on listing pages are served from our own
media - Steam's CDN can 403, 404 or rate-limit without any user-visible effect.

Resumable and idempotent: rows with an existing thumbnail are skipped, so this
is safe to run repeatedly (and 'ensure_ready' runs a bounded slice each boot).

    python manage.py materialize_images                     # everything
    python manage.py materialize_images --limit 2000        # bounded slice
    python manage.py materialize_images --workers 16
"""
import concurrent.futures as futures
import time

from django.core.management.base import BaseCommand

from store import artwork
from store.models import ProductImage


class Command(BaseCommand):
    help = "Download hotlinked artwork once and store local WebP thumbnails."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0,
                            help="Max images to materialize this run (0 = all).")
        parser.add_argument("--workers", type=int, default=16,
                            help="Concurrent downloads/conversions.")
        parser.add_argument("--timeout", type=int, default=15,
                            help="Per-download timeout in seconds.")

    def handle(self, *args, **options):
        limit = options["limit"]
        workers = max(1, options["workers"])
        timeout = options["timeout"]
        verbosity = options["verbosity"]

        qs = (ProductImage.objects.exclude(external_url="")
              .filter(thumbnail="", art_unavailable=False)
              .select_related("product")
              .order_by("id")
              .values_list("id", "product_id", "external_url",
                           "product__steam_app_id"))
        if limit:
            qs = qs[:limit]
        targets = list(qs)
        total = len(targets)
        if not total:
            self.stdout.write(self.style.SUCCESS(
                "Nothing to materialize - every image already has a local thumbnail."))
            return
        self.stdout.write(
            f"Materializing {total:,} thumbnail(s) with {workers} download "
            "worker(s); DB writes stay on the main thread (1 connection)...")

        ok = failed = unavailable = 0
        dead_pks = []
        started = time.time()

        def fetch(target):
            """Worker: network + CPU only. Returns (target, webp_bytes, url)."""
            pk, product_id, external_url, appid = target
            webp, url = artwork.fetch_thumbnail(
                external_url, appid=appid, timeout=timeout)
            return target, webp, url

        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(fetch, t): t for t in targets}
            for fut in futures.as_completed(pending):
                pk, product_id, external_url, _appid = pending[fut]
                try:
                    (_t, webp, url) = fut.result()
                except artwork.ArtworkUnavailable:
                    unavailable += 1
                    dead_pks.append(pk)
                    continue
                except artwork.ArtworkError as exc:
                    failed += 1
                    if verbosity >= 2:
                        self.stderr.write(f"  [{pk}] {exc}")
                    continue
                except Exception as exc:
                    failed += 1
                    if verbosity >= 2:
                        self.stderr.write(f"  [{pk}] {type(exc).__name__}: {exc}")
                    continue
                if webp is None:
                    failed += 1
                    continue
                try:
                    row = ProductImage(id=pk, product_id=product_id)
                    name = artwork.save_thumbnail(row, webp, save=False)
                    fields = {"thumbnail": name}
                    if url != external_url:
                        fields["external_url"] = url
                    ProductImage.objects.filter(pk=pk).update(**fields)
                    ok += 1
                except Exception as exc:
                    failed += 1
                    self.stderr.write(f"  [{pk}] save failed: {type(exc).__name__}: {exc}")

        if dead_pks:
            ProductImage.objects.filter(pk__in=dead_pks).update(art_unavailable=True)

        remaining = (ProductImage.objects.exclude(external_url="")
                     .filter(thumbnail="", art_unavailable=False).count())
        summary = (f"Done in {time.time() - started:.0f}s: materialized={ok:,} "
                   f"unavailable={unavailable:,} (delisted, won't retry) "
                   f"failed={failed:,} - {remaining:,} retryable row(s) still to go.")
        if failed and verbosity < 2:
            summary += " (transient misses are expected; -v2 lists each)."
        self.stdout.write(self.style.SUCCESS(summary))