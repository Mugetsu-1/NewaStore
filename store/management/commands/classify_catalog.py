"""Split non-game software out of the games catalog into an Applications category.

The catalog was seeded wholesale from SteamSpy, so it contains a long tail of
non-game software (video editors, DAWs, art tools, utilities) alongside the
games. This command identifies those entries and moves them to a dedicated
``Applications`` category, flagging them ``product_type='application'`` so the
storefront's games surfaces (home, default shop, recommendations) stop showing
them while they remain fully browsable under their own category.

Signals, cheap and authoritative first:
    1. SteamSpy software genres (Utilities, Design & Illustration, Video
       Production, ...) — Steam itself filed these apps under a software genre.
       ~11 API calls total; empty genres are skipped.
    2. A tight software brand-name net over titles (CyberLink, FL Studio,
       Wallpaper Engine, RPG Maker, ...) — offline, near-zero false positives.
    3. ``--confirm`` (optional, slower): verify each candidate against the Steam
       appdetails ``type`` field and drop anything Steam still calls a game.

Curated AAA appids are always excluded as a safety net. The command only
promotes rows to Applications (never silently demotes back), so a run with
SteamSpy unavailable can't wrongly flip apps back into the games catalog.

    python manage.py classify_catalog --dry-run        # preview, change nothing
    python manage.py classify_catalog                  # genres + keyword net
    python manage.py classify_catalog --keywords-only  # offline, titles only
    python manage.py classify_catalog --confirm        # verify via Steam type
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from store.models import Product, Category
from store.importers import (SOFTWARE_GENRES, STEAM_APPLICATION_TYPES, CURATED_AAA,
                             steamspy_genre_rows, looks_like_application,
                             api, STEAM_APPDETAILS)

APPLICATIONS_CATEGORY = 'Applications'


class Command(BaseCommand):
    help = "Move non-game software out of the games catalog into an Applications category."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Show what would change without writing anything.")
        parser.add_argument('--keywords-only', action='store_true',
                            help="Skip the SteamSpy genre network pass; use only "
                                 "the title brand-name net (offline).")
        parser.add_argument('--confirm', action='store_true',
                            help="Verify each candidate against the Steam appdetails "
                                 "'type' and drop anything Steam still calls a game "
                                 "(slower — one API call per candidate).")
        parser.add_argument('--batch', type=int, default=1000,
                            help="Rows per bulk_update batch (default: 1000).")

    def handle(self, *args, **options):
        dry = options['dry_run']
        batch_size = max(1, options['batch'])
        self.stdout.write(f"Classifying catalog{' [DRY RUN]' if dry else ''}")

        candidate_appids = set()
        if not options['keywords_only']:
            for genre in SOFTWARE_GENRES:
                rows = steamspy_genre_rows(genre)
                if not rows:
                    self.stdout.write(self.style.WARNING(f"  {genre}: no data — skipped."))
                    continue
                candidate_appids |= set(rows.keys())
                self.stdout.write(f"  {genre}: {len(rows)} software apps listed.")

        # Products flagged by SteamSpy software genres.
        by_genre = set()
        if candidate_appids:
            by_genre = set(Product.objects.filter(
                steam_app_id__in=candidate_appids).values_list('id', flat=True))

        # Products flagged by the title brand-name net.
        by_keyword = set()
        name_qs = Product.objects.all().only('id', 'name').iterator(chunk_size=2000)
        for p in name_qs:
            if looks_like_application(p.name):
                by_keyword.add(p.id)

        app_ids = by_genre | by_keyword

        # Safety net: never demote a curated AAA blockbuster.
        curated = set(Product.objects.filter(
            steam_app_id__in=list(CURATED_AAA.keys())).values_list('id', flat=True))
        app_ids -= curated

        self.stdout.write("")
        self.stdout.write(f"Candidates: {len(by_genre)} by software genre, "
                          f"{len(by_keyword)} by brand-name net "
                          f"(union {len(by_genre | by_keyword)}, "
                          f"{len(curated & (by_genre | by_keyword))} curated-AAA kept as games).")

        if options['confirm'] and app_ids:
            app_ids = self._confirm_via_steam(app_ids)

        self._apply(app_ids, dry, batch_size)

    def _confirm_via_steam(self, app_ids):
        """Drop candidates whose Steam appdetails 'type' is still a game.

        Conservative on failure: brand-net hits without an appid, and any
        candidates left unchecked when Steam starts throttling, stay flagged as
        applications rather than being silently dropped back into the catalog.
        """
        rows = list(Product.objects.filter(id__in=app_ids)
                    .values_list('id', 'steam_app_id'))
        with_appid = [(pid, appid) for pid, appid in rows if appid]
        confirmed = {pid for pid, appid in rows if not appid}
        self.stdout.write(f"  confirming {len(with_appid)} candidate(s) via Steam "
                          f"(keeping {len(confirmed)} brand-net hit(s) without an appid)...")
        checked = dropped = 0
        for i, (pid, appid) in enumerate(with_appid):
            data = api.get_json(STEAM_APPDETAILS, {'appids': appid},
                                min_interval=0.65, retries=2, ok_429=False)
            if data == 'RATE_LIMITED':
                remaining = {p for p, _ in with_appid[i:]}
                confirmed |= remaining
                self.stdout.write(self.style.WARNING(
                    f"    Steam throttling — keeping {len(remaining)} remaining "
                    "candidate(s) unverified."))
                break
            checked += 1
            payload = (data or {}).get(str(appid), {})
            stype = ((payload.get('data') or {}).get('type') or '').strip().lower()
            if stype and stype not in STEAM_APPLICATION_TYPES:
                dropped += 1        # Steam still calls it a game/dlc/demo.
            else:
                confirmed.add(pid)
        self.stdout.write(f"  confirmed {len(confirmed)} application(s); "
                          f"dropped {dropped} the store still calls games "
                          f"({checked} checked).")
        return confirmed

    @transaction.atomic
    def _apply(self, app_ids, dry, batch_size):
        apps_cat = self._applications_category(dry)
        target = Product.objects.filter(id__in=app_ids)
        total = target.count()

        already = target.filter(
            product_type=Product.PRODUCT_TYPE_APPLICATION,
            category=apps_cat).count() if apps_cat else 0

        samples = list(target.exclude(product_type=Product.PRODUCT_TYPE_APPLICATION)
                       .values_list('name', flat=True)[:12])
        self.stdout.write("")
        self.stdout.write(f"Applications identified: {total} "
                          f"({already} already classified).")
        if samples:
            self.stdout.write("Newly reclassified sample:")
            for name in samples:
                self.stdout.write(f"  - {name[:60]}")

        if dry:
            self.stdout.write(self.style.SUCCESS(
                f"[DRY RUN] Would move {total - already} product(s) into "
                f"'{APPLICATIONS_CATEGORY}'."))
            return

        updated = target.exclude(
            product_type=Product.PRODUCT_TYPE_APPLICATION,
            category=apps_cat).update(
            product_type=Product.PRODUCT_TYPE_APPLICATION, category=apps_cat)
        self.stdout.write(self.style.SUCCESS(
            f"Classified {total} application(s); {updated} row(s) updated, "
            f"now under '{APPLICATIONS_CATEGORY}'."))

    def _applications_category(self, dry):
        existing = Category.objects.filter(name=APPLICATIONS_CATEGORY).first()
        if existing or dry:
            return existing
        return Category.objects.create(
            name=APPLICATIONS_CATEGORY,
            description="Software, creative tools and utilities — non-game "
                        "applications available as digital downloads.",
            is_active=True, sort_order=90)
