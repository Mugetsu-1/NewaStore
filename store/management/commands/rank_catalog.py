"""Rank the catalog by real popularity (SteamSpy owners) + a curated AAA list.

Replaces the old price-derived tiering (a $60 sticker no longer implies "AAA").
Pulls estimated owner counts from SteamSpy's per-genre feed — the same endpoint
the importer already uses — stores them on ``Product.owners``, and recomputes
each game's prominence tier:

    curated blockbuster or >=5,000,000 owners  -> AAA
    >=500,000 owners                           -> AA
    priced / >=20,000 owners                   -> Indie
    free and obscure                           -> Free

It then marks the real AAA games (curated titles + the biggest by owners, that
actually have cover art) as ``is_featured`` for the homepage, clearing the flag
from everything else. Prices are left untouched — see ``reprice_catalog``.

    python manage.py rank_catalog --dry-run     # preview tiers + featured
    python manage.py rank_catalog               # fetch owners, tier, feature
    python manage.py rank_catalog --skip-fetch  # re-tier from stored owners only
    python manage.py rank_catalog --feature 0   # tier only, don't touch featured
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from store.models import Product
from store.importers import (CURATED_AAA, steamspy_genre_rows, parse_owners,
                             tier_for_owners)

TIER_NAME = dict(Product.TIER_CHOICES)

# Steam's game genres (mirrors import_steamspy). SteamSpy returns the top ~1000
# apps per genre with owner estimates — union covers the popular catalogue.
GAME_GENRES = [
    'Action', 'Adventure', 'Casual', 'Free to Play', 'Indie',
    'Massively Multiplayer', 'Racing', 'RPG', 'Simulation', 'Sports',
    'Strategy', 'Early Access',
]


class Command(BaseCommand):
    help = "Rank the catalog by SteamSpy owners + a curated AAA list (tier + featured)."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Show what would change without writing anything.")
        parser.add_argument('--skip-fetch', action='store_true',
                            help="Don't hit SteamSpy; re-tier from stored owners.")
        parser.add_argument('--batch', type=int, default=2000,
                            help="Rows per bulk_update batch (default: 2000).")
        parser.add_argument('--feature', type=int, default=12,
                            help="How many top games (beyond curated AAA) to mark "
                                 "is_featured. 0 leaves is_featured untouched.")

    def handle(self, *args, **options):
        dry = options['dry_run']
        batch_size = max(1, options['batch'])

        owners_map = {}
        if not options['skip_fetch']:
            owners_map = self._fetch_owners()
        else:
            self.stdout.write("Skipping SteamSpy fetch — re-tiering from stored owners.")

        self._rank(owners_map, dry, batch_size)

        n = options['feature']
        if n:
            self._feature(n, dry)
        else:
            self.stdout.write("Leaving is_featured untouched (--feature 0).")

    def _fetch_owners(self):
        """Union of SteamSpy per-genre owner estimates: {appid: owners_midpoint}."""
        owners = {}
        for genre in GAME_GENRES:
            rows = steamspy_genre_rows(genre)
            if not rows:
                self.stdout.write(self.style.WARNING(f"  {genre}: no data — skipped."))
                continue
            for appid, row in rows.items():
                val = parse_owners(row.get('owners'))
                if val > owners.get(appid, 0):
                    owners[appid] = val
            self.stdout.write(f"  {genre}: {len(rows)} apps.")
        self.stdout.write(f"Owner estimates for {len(owners)} app(s).")
        return owners

    def _rank(self, owners_map, dry, batch_size):
        tier_counts = Counter()
        samples = []
        batch = []
        changed = 0
        curated_ids = set(CURATED_AAA)

        qs = Product.objects.all().only(
            'id', 'name', 'steam_app_id', 'price', 'owners', 'tier', 'product_type')
        for p in qs.iterator(chunk_size=batch_size):
            new_owners = owners_map.get(p.steam_app_id, p.owners or 0)
            curated = (p.steam_app_id in curated_ids
                       and p.product_type == Product.PRODUCT_TYPE_GAME)
            has_price = (p.price or 0) > 0
            new_tier = tier_for_owners(new_owners, has_price, curated)

            if p.product_type == Product.PRODUCT_TYPE_GAME:
                tier_counts[new_tier] += 1
            if len(samples) < 8 and (curated or new_owners >= 500_000):
                samples.append((p.name[:34], new_owners, TIER_NAME[new_tier]))

            if p.owners != new_owners or p.tier != new_tier:
                changed += 1
            p.owners, p.tier = new_owners, new_tier
            if not dry:
                batch.append(p)
                if len(batch) >= batch_size:
                    Product.objects.bulk_update(batch, ['owners', 'tier'])
                    batch.clear()
        if not dry and batch:
            Product.objects.bulk_update(batch, ['owners', 'tier'])

        self.stdout.write("")
        self.stdout.write("Top games (name | owners | tier):")
        for name, own, tier in samples:
            self.stdout.write(f"  {name:<36} {own:>12,}  [{tier}]")
        self.stdout.write("")
        self.stdout.write("Game tier distribution:")
        for tier in (Product.TIER_AAA, Product.TIER_AA, Product.TIER_INDIE, Product.TIER_FREE):
            self.stdout.write(f"  {TIER_NAME[tier]:<6}: {tier_counts.get(tier, 0)}")
        verb = "Would re-tier" if dry else "Re-tiered"
        self.stdout.write(self.style.SUCCESS(f"{verb} {changed} product(s)."))

    def _with_art(self, qs):
        """Games that actually have cover art (so featured cards aren't blank)."""
        return (qs.filter(is_active=True, product_type=Product.PRODUCT_TYPE_GAME)
                .filter(images__art_unavailable=False)
                .filter(Q(images__thumbnail__gt='') | Q(images__external_url__gt=''))
                .distinct())

    def _feature(self, n, dry):
        """Feature curated AAA games + the top-N games by owners (with art)."""
        curated = list(self._with_art(
            Product.objects.filter(steam_app_id__in=list(CURATED_AAA.keys())))
            .values_list('id', flat=True))
        top = list(self._with_art(Product.objects.all())
                   .order_by('-owners', '-tier', '-published_at', '-id')
                   [:max(0, n)].values_list('id', flat=True))
        feature_ids = set(curated) | set(top)

        self.stdout.write("")
        self.stdout.write(f"Featured: {len(curated)} curated AAA + up to {n} top "
                          f"games = {len(feature_ids)} game(s) with art.")
        if dry:
            names = list(Product.objects.filter(id__in=feature_ids)
                         .values_list('name', flat=True)[:12])
            self.stdout.write("Would feature: " + ", ".join(names))
            return

        with transaction.atomic():
            Product.objects.filter(is_featured=True).exclude(
                id__in=feature_ids).update(is_featured=False)
            Product.objects.filter(id__in=feature_ids).update(is_featured=True)
        self.stdout.write(self.style.SUCCESS(
            f"Marked {len(feature_ids)} game(s) as featured."))

