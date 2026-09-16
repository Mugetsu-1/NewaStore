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

        qs = (ProductImage.objects.exclude(external_url="").filter(thumbnail="")
              .order_by("id"))
        if limit:
            qs = qs[:limit]
        targets = list(qs.values_list("id", flat=True))
        total = len(targets)
        if not total:
            self.stdout.write(self.style.SUCCESS(
                "Nothing to materialize - every image already has a local thumbnail."))
            return
        self.stdout.write(f"Materializing {total:,} thumbnail(s) with {workers} workers...")

        ok = failed = 0
        started = time.time()

        def run(pk):
            row = ProductImage.objects.only("id", "product_id", "external_url").get(pk=pk)
            return artwork.materialize_row(row, timeout=timeout)

        with futures.ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(run, pk): pk for pk in targets}
            for fut in futures.as_completed(pending):
                try:
                    if fut.result():
                        ok += 1
                    else:
                        failed += 1  # no external_url (should not happen here)
                except artwork.ArtworkError as exc:
                    failed += 1
                    self.stderr.write(f"  [{pending[fut]}] {exc}")
                except Exception as exc:  # noqa: BLE001 - keep the sweep going
                    failed += 1
                    self.stderr.write(f"  [{pending[fut]}] {type(exc).__name__}: {exc}")

        remaining = (ProductImage.objects.exclude(external_url="").filter(thumbnail="")
                     .count())
        self.stdout.write(self.style.SUCCESS(
            f"Done in {time.time() - started:.0f}s: materialized={ok:,} "
            f"failed={failed:,} - {remaining:,} row(s) still to go."))