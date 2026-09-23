"""Make the project launch-ready: migrations, catalog, artwork.

Runs everything needed for a fully populated, fully illustrated catalog and is
deliberately *incremental*: each step checks current state first, so a restart
with nothing to do finishes in milliseconds. Heavy steps are additionally
rate-limited through a state file, so a flaky network can never turn into a
multi-minute boot on every restart.

Pipeline
--------
1. Migrations - applies pending migrations non-interactively.
2. Catalog    - runs ``import_steamspy`` when the catalog is empty (the import
                is resumable via ``.steamspy_progress``).
3. Art repair - runs ``repair_missing_images`` when products show the
                placeholder, unless the same problem was attempted recently
                (TTL 12h) or the placeholder count has not changed.
4. Thumbnails - runs ``materialize_images``: downloads each hotlinked capsule
                once and stores a local WebP, so listing pages are served from
                our own media and never depend on Steam's CDN at request time.
                Resumable (rows with a thumbnail are skipped), so it makes
                steady progress across restarts until every card is local.
5. URL audit  - probes stored artwork URLs for dead links through
                ``audit_product_images``: a bounded slice per run (default
                5,000 URLs), resumable, so the 82k-URL catalog is swept over a
                few restarts instead of blocking one boot.

    python manage.py ensure_ready                 # normal boot work
    python manage.py ensure_ready --force         # ignore cadence markers
    python manage.py ensure_ready --skip-import --skip-repair --skip-audit
    python manage.py ensure_ready --audit-limit 20000 --workers 32
    python manage.py ensure_ready --materialize-limit 4000   # cap the sweep
"""
import json
import time
from datetime import timedelta

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Q
from django.utils import timezone

from store.models import Product, ProductImage

STATE_FILE = settings.BASE_DIR / ".bootstrap_state.json"
REPAIR_TTL = timedelta(hours=12)      # min interval between repair attempts
AUDIT_TTL = timedelta(days=7)         # re-sweep stored URLs weekly
DEFAULT_AUDIT_SLICE = 5000            # URLs probed per run


class Command(BaseCommand):
    help = "Apply migrations, import the catalog and repair artwork as needed."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="Run repair/audit now, ignoring cadence markers.")
        parser.add_argument("--skip-migrate", action="store_true")
        parser.add_argument("--skip-import", action="store_true")
        parser.add_argument("--skip-repair", action="store_true")
        parser.add_argument("--skip-materialize", action="store_true")
        parser.add_argument("--skip-audit", action="store_true")
        parser.add_argument("--repair-limit", type=int, default=0,
                            help="Max products to repair this run (0 = all).")
        parser.add_argument("--materialize-limit", type=int, default=0,
                            help="Max thumbnails to materialize this run (0 = all remaining).")
        parser.add_argument("--audit-limit", type=int, default=DEFAULT_AUDIT_SLICE,
                            help="URLs probed per audit run.")
        parser.add_argument("--workers", type=int, default=24)

    # ------------------------------------------------------------ state I/O

    def _load_state(self):
        if STATE_FILE.exists():
            try:
                with STATE_FILE.open(encoding="utf-8") as fh:
                    state = json.load(fh)
                if isinstance(state, dict):
                    return state
            except (OSError, ValueError):
                pass
        return {"audit_after_id": 0, "audit_completed_at": None,
                "repair_last_at": None, "repair_last_count": None,
                "import_last_at": None}

    def _save_state(self, state):
        with STATE_FILE.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)

    # ------------------------------------------------------------ utilities

    def _placeholder_count(self):
        return Product.objects.filter(
            Q(images__isnull=True) | Q(images__external_url="")).distinct().count()

    def _pending_migrations(self):
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        return executor.migration_plan(targets)

    def _parse_iso(self, value):
        try:
            return timezone.datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None

    def _ensure_superuser(self):
        """Make the admin account match the credentials in the environment.

        Credentials come from env (DJANGO_SUPERUSER_USERNAME / _EMAIL /
        _PASSWORD), falling back to admin/admin for a bare local checkout.
        This is authoritative and idempotent: the account is created when
        missing and otherwise has its email, staff/superuser flags and
        password reset to match, so /admin login always agrees with .env -
        including right after a users-only reset. A default password is fine
        for a localhost project but must be changed before any real
        deployment, so we say so loudly.
        """
        User = get_user_model()
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@newastore.local")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin")
        user, created = User.objects.get_or_create(
            username=username, defaults={"email": email})
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()
        verb = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(
            f"[1b/6] Admin account {verb}: username='{username}'."))
        if password == "admin":
            self.stdout.write(self.style.WARNING(
                "        Default password in use - set DJANGO_SUPERUSER_PASSWORD "
                "in .env and change it in /admin before deploying."))

    def _ensure_demo_user(self):
        """Make the demo customer match DEMO_USER_* from the environment.

        Skipped entirely when DEMO_USER_PASSWORD is unset, so a deployment
        that wants no demo account simply omits it. Otherwise the account is
        created or reset in the same authoritative way as the admin account,
        but as a plain (non-staff) shopper.
        """
        password = os.environ.get("DEMO_USER_PASSWORD")
        if not password:
            return
        User = get_user_model()
        username = os.environ.get("DEMO_USER_USERNAME", "demo")
        email = os.environ.get("DEMO_USER_EMAIL", "demo@newastore.local")
        user, created = User.objects.get_or_create(
            username=username, defaults={"email": email})
        user.email = email
        user.is_staff = False
        user.is_superuser = False
        user.set_password(password)
        user.save()
        verb = "created" if created else "updated"
        self.stdout.write(self.style.SUCCESS(
            f"[1b/6] Demo customer {verb}: username='{username}'."))

    def _ensure_site_email(self):
        """Seed the store's public contact email so the DB value is correct."""
        from store.models import SiteSettings
        target = "newastore8@gmail.com"
        site = SiteSettings.get_settings()
        if site.email != target:
            site.email = target
            site.save(update_fields=["email"])
            self.stdout.write(self.style.SUCCESS(
                f"[1b/6] Site contact email set to {target}."))

    def _normalize_dead_capsule_urls(self):
        """Bulk-swap the dead `capsule_616x353.jpg` pattern for `header.jpg`.

        No network calls: a single UPDATE across every row still stored on the
        capsule pattern. header.jpg is the universal Steam art file, so this
        makes those cards render immediately while the local-WebP materialize
        step localizes them for good. Only touches rows not yet localized.
        """
        qs = ProductImage.objects.filter(
            external_url__contains="capsule_616x353", thumbnail="")
        fixed = 0
        # F-expression string replace keeps it to one SQL statement on PostgreSQL.
        from django.db.models.functions import Replace
        from django.db.models import Value
        fixed = qs.update(external_url=Replace(
            "external_url", Value("capsule_616x353.jpg"), Value("header.jpg")))
        return fixed

    # ---------------------------------------------------------------- main

    def handle(self, *args, **options):
        force = options["force"]
        state = self._load_state()
        started = time.time()
        notes = []

        # ---- 1. migrations --------------------------------------------------
        if not options["skip_migrate"]:
            pending = self._pending_migrations()
            if pending:
                self.stdout.write(self.style.MIGRATE_HEADING(
                    f"[1/6] Applying {len(pending)} pending migration(s)..."))
                call_command("migrate", interactive=False, verbosity=0)
                self.stdout.write(self.style.SUCCESS(f"  applied {len(pending)}."))
                notes.append(f"migrations={len(pending)}")
            else:
                self.stdout.write("[1/6] Migrations: up to date.")

        # ---- 1b. accounts + store email (so /admin login always works) -----
        self._ensure_superuser()
        self._ensure_demo_user()
        self._ensure_site_email()

        # ---- 2. catalog import (only when the DB is empty) -------------------
        product_count = Product.objects.count()
        if not options["skip_import"]:
            if product_count == 0:
                self.stdout.write(self.style.WARNING(
                    "[2/6] Catalog is empty - importing the full SteamSpy catalog.\n"
                    "      This can take 15-20 minutes. The server stays up, and the\n"
                    "      import resumes from its marker if it is interrupted."))
                call_command("import_steamspy", stdout=self.stdout, stderr=self.stderr)
                product_count = Product.objects.count()
                state["import_last_at"] = timezone.now().isoformat()
                notes.append("import=full")
            else:
                self.stdout.write(
                    f"[2/6] Catalog: {product_count:,} products present - import skipped.")
        else:
            product_count = Product.objects.count()

        # ---- 3. artwork repair ------------------------------------------------
        placeholders = self._placeholder_count()
        if not options["skip_repair"]:
            last_at = self._parse_iso(state.get("repair_last_at"))
            last_count = state.get("repair_last_count")
            cadence_ok = (
                force
                or last_at is None
                or last_count != placeholders
                or (timezone.now() - last_at) > REPAIR_TTL
            )
            if placeholders and cadence_ok:
                self.stdout.write(self.style.WARNING(
                    f"[3/6] Repairing artwork for {placeholders:,} placeholder product(s)..."))
                call_command("repair_missing_images",
                             limit=options["repair_limit"], workers=options["workers"],
                             stdout=self.stdout, stderr=self.stderr)
                placeholders = self._placeholder_count()
                state["repair_last_at"] = timezone.now().isoformat()
                state["repair_last_count"] = placeholders
                notes.append(f"repair->placeholders={placeholders}")
            elif placeholders:
                self.stdout.write(
                    f"[3/6] Artwork repair: {placeholders:,} placeholder(s) known; "
                    "retry skipped (attempted recently). Use --force to retry now.")
            else:
                self.stdout.write("[3/6] Artwork repair: nothing to do.")
        else:
            placeholders = self._placeholder_count()

        # ---- 4. normalize dead capsule URLs -> header.jpg (instant) ---------
        # The SteamSpy import stored `capsule_616x353.jpg`, which 404s for a
        # large slice of older apps. `header.jpg` is the universal Steam art
        # file, so a single bulk UPDATE (no network) makes those cards render
        # immediately - important on the async path, where the site may serve
        # before materialize has localized every row.
        fixed = self._normalize_dead_capsule_urls()
        if fixed:
            self.stdout.write(self.style.SUCCESS(
                f"[4/6] Normalized {fixed:,} dead capsule URL(s) -> header.jpg."))
            notes.append(f"url_normalize={fixed}")
        else:
            self.stdout.write("[4/6] Image URLs: no dead capsule pattern to normalize.")

        # ---- 5. materialize local WebP thumbnails (fetch-once) --------------
        # Download every hotlinked image once and store a local WebP so listing
        # pages stop depending on Steam's CDN (the permanent fix for thumbnails
        # that 403/404/rate-limit). Resumable: rows that already carry a
        # thumbnail are skipped, so this only ever does new work.
        if not options["skip_materialize"]:
            pending_thumbs = (ProductImage.objects.exclude(external_url="")
                              .filter(thumbnail="", art_unavailable=False).count())
            if pending_thumbs:
                slice_note = (f"the next {options['materialize_limit']:,} of "
                              if options["materialize_limit"] else "all ")
                self.stdout.write(self.style.WARNING(
                    f"[5/6] Materializing {slice_note}{pending_thumbs:,} card "
                    "thumbnail(s) into local WebP..."))
                call_command("materialize_images",
                             limit=options["materialize_limit"],
                             workers=options["workers"],
                             stdout=self.stdout, stderr=self.stderr)
                remaining_thumbs = (ProductImage.objects.exclude(external_url="")
                                    .filter(thumbnail="", art_unavailable=False).count())
                notes.append(f"materialize->pending={remaining_thumbs}")
            else:
                self.stdout.write("[5/6] Thumbnails: every card already local.")

        self._state_after_handle = (state, options, notes, placeholders, started)

        # ---- 5. resumable URL audit ----------------------------------------
        if not options["skip_audit"]:
            after_id = int(state.get("audit_after_id") or 0)
            completed_at = self._parse_iso(state.get("audit_completed_at"))
            sweep_fresh = (after_id == 0 and completed_at is not None
                           and (timezone.now() - completed_at) < AUDIT_TTL)
            if sweep_fresh and not force:
                days = (timezone.now() - completed_at).days
                self.stdout.write(
                    f"[6/6] URL audit: full sweep finished {days} day(s) ago - skipped.")
            else:
                remaining = ProductImage.objects.exclude(external_url="").filter(
                    id__gt=after_id).count()
                if remaining:
                    slice_size = min(remaining, options["audit_limit"])
                    self.stdout.write(self.style.WARNING(
                        f"[6/6] URL audit: probing the next {slice_size:,} of "
                        f"{remaining:,} stored URL(s)..."))
                    call_command("audit_product_images",
                                 after_id=after_id, limit=options["audit_limit"],
                                 workers=options["workers"],
                                 stdout=self.stdout, stderr=self.stderr)
                    window = list(ProductImage.objects.exclude(external_url="")
                                  .filter(id__gt=after_id).order_by("id")
                                  .values_list("id", flat=True)[:options["audit_limit"]])
                    if window:
                        state["audit_after_id"] = window[-1]
                        notes.append(f"audit_after_id={window[-1]}")
                    else:
                        state["audit_after_id"] = 0
                        state["audit_completed_at"] = timezone.now().isoformat()
                        notes.append("audit=sweep complete")
                else:
                    state["audit_after_id"] = 0
                    state["audit_completed_at"] = timezone.now().isoformat()
                    self.stdout.write("[6/6] URL audit: sweep complete.")

        self._save_state(state)

        elapsed = time.time() - started
        summary = f"Bootstrap finished in {elapsed:.1f}s"
        if notes:
            summary += ": " + ", ".join(notes)
        self.stdout.write(self.style.SUCCESS(summary))