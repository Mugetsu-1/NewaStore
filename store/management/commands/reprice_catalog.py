"""Re-tier and re-price the whole catalog with clean, reasonable NPR prices.

The catalog was originally imported with a raw usd*rate conversion, which
produced odd figures (Rs 1,349, Rs 8,097, ...) and left every row in the
default tier. This command derives the source USD price back from each stored
NPR price and re-applies the shared tiered pricing (see
``store.importers.tiered_prices``), so the storefront shows round, sensible
prices and AAA titles are tagged for prominence.

    python manage.py reprice_catalog --dry-run      # preview, change nothing
    python manage.py reprice_catalog                # apply pricing + tiers
    python manage.py reprice_catalog --feature 0    # reprice but don't touch is_featured

Run once after the initial import; future imports already write tiered prices.
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from store.models import Product
from store.importers import USD_TO_NPR, tiered_prices

TIER_NAME = dict(Product.TIER_CHOICES)
FIELDS = ['price', 'discount_price', 'cost_price', 'tier']


class Command(BaseCommand):
    help = "Re-tier and re-price the catalog with clean, tiered NPR prices."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Show what would change without writing anything.")
        parser.add_argument('--batch', type=int, default=2000,
                            help="Rows per bulk_update batch (default: 2000).")
        parser.add_argument('--feature', type=int, default=12,
                            help="How many top AAA titles (with artwork) to mark "
                                 "is_featured. 0 leaves is_featured untouched.")

    def handle(self, *args, **options):
        dry = options['dry_run']
        batch_size = max(1, options['batch'])
        total = Product.objects.count()
        self.stdout.write(f"Repricing {total} product(s) "
                          f"(rate USD->NPR = {USD_TO_NPR}){' [DRY RUN]' if dry else ''}")

        tier_counts = Counter()
        samples = []
        batch = []
        changed = 0
        processed = 0

        qs = Product.objects.all().only(
            'id', 'name', 'price', 'discount_price', 'cost_price', 'tier')
        for p in qs.iterator(chunk_size=batch_size):
            price_usd = (p.price or 0) / USD_TO_NPR
            disc_usd = (p.discount_price / USD_TO_NPR) if p.discount_price else None
            new_price, new_disc, new_cost, new_tier = tiered_prices(price_usd, disc_usd)

            tier_counts[new_tier] += 1
            if len(samples) < 8:
                samples.append((p.name[:38], p.price, new_price, TIER_NAME[new_tier]))

            if (p.price != new_price or p.discount_price != new_disc
                    or p.cost_price != new_cost or p.tier != new_tier):
                changed += 1
            p.price, p.discount_price, p.cost_price, p.tier = (
                new_price, new_disc, new_cost, new_tier)

            if not dry:
                batch.append(p)
                if len(batch) >= batch_size:
                    Product.objects.bulk_update(batch, FIELDS)
                    batch.clear()
            processed += 1

        if not dry and batch:
            Product.objects.bulk_update(batch, FIELDS)

        self.stdout.write("")
        self.stdout.write("Sample (name | old -> new | tier):")
        for name, old, new, tier in samples:
            self.stdout.write(f"  {name:<40} Rs {old} -> Rs {new}  [{tier}]")
        self.stdout.write("")
        self.stdout.write("Tier distribution:")
        for tier in (Product.TIER_AAA, Product.TIER_AA, Product.TIER_INDIE, Product.TIER_FREE):
            self.stdout.write(f"  {TIER_NAME[tier]:<6}: {tier_counts.get(tier, 0)}")

        n = options['feature']
        if n and not dry:
            featured_ids = self._feature_top_aaa(n)
            self.stdout.write(self.style.SUCCESS(
                f"Marked {len(featured_ids)} AAA title(s) as featured."))
        elif n and dry:
            preview = list(self._top_aaa_qs()[:n].values_list('name', flat=True))
            self.stdout.write("")
            self.stdout.write(f"Would feature {len(preview)} AAA title(s): "
                              + ", ".join(preview[:n]))

        verb = "Would reprice" if dry else "Repriced"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {changed} of {processed} product(s)."))

    def _top_aaa_qs(self):
        """Active AAA games that actually have artwork, best first."""
        return (Product.objects
                .filter(is_active=True, tier=Product.TIER_AAA)
                .filter(images__art_unavailable=False)
                .filter(Q(images__thumbnail__gt='') | Q(images__external_url__gt=''))
                .distinct()
                .order_by('-price', '-published_at', '-id'))

    @transaction.atomic
    def _feature_top_aaa(self, n):
        ids = list(self._top_aaa_qs()[:n].values_list('id', flat=True))
        Product.objects.filter(is_featured=True).exclude(id__in=ids).update(is_featured=False)
        Product.objects.filter(id__in=ids).update(is_featured=True)
        return ids
