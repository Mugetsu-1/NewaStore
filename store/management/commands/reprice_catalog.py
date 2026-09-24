"""Re-price the whole catalog with clean, reasonable NPR prices.

The catalog was originally imported with a raw usd*rate conversion, which
produced odd figures (Rs 1,349, Rs 8,097, ...). This command derives the source
USD price back from each stored NPR price and re-applies the shared tiered
pricing (see ``store.importers.tiered_prices``), so the storefront shows round,
sensible prices.

Pricing only: prominence tiers and the featured flag are owned by
``rank_catalog`` (real SteamSpy popularity + a curated AAA list), so a
free-to-play megahit is never given an AAA price and a pricey obscure title is
never featured. Price and tier are deliberately decoupled.

    python manage.py reprice_catalog --dry-run      # preview, change nothing
    python manage.py reprice_catalog                # apply clean prices

Run once after the initial import; future imports already write tiered prices.
"""
from django.core.management.base import BaseCommand

from store.models import Product
from store.importers import USD_TO_NPR, tiered_prices

FIELDS = ['price', 'discount_price', 'cost_price']


class Command(BaseCommand):
    help = "Re-price the catalog with clean, tiered NPR prices (price only)."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Show what would change without writing anything.")
        parser.add_argument('--batch', type=int, default=2000,
                            help="Rows per bulk_update batch (default: 2000).")

    def handle(self, *args, **options):
        dry = options['dry_run']
        batch_size = max(1, options['batch'])
        total = Product.objects.count()
        self.stdout.write(f"Repricing {total} product(s) "
                          f"(rate USD->NPR = {USD_TO_NPR}){' [DRY RUN]' if dry else ''}")

        samples = []
        batch = []
        changed = 0
        processed = 0

        qs = Product.objects.all().only(
            'id', 'name', 'price', 'discount_price', 'cost_price')
        for p in qs.iterator(chunk_size=batch_size):
            price_usd = (p.price or 0) / USD_TO_NPR
            disc_usd = (p.discount_price / USD_TO_NPR) if p.discount_price else None
            new_price, new_disc, new_cost, _tier = tiered_prices(price_usd, disc_usd)

            if len(samples) < 8:
                samples.append((p.name[:38], p.price, new_price))

            if (p.price != new_price or p.discount_price != new_disc
                    or p.cost_price != new_cost):
                changed += 1
            p.price, p.discount_price, p.cost_price = new_price, new_disc, new_cost

            if not dry:
                batch.append(p)
                if len(batch) >= batch_size:
                    Product.objects.bulk_update(batch, FIELDS)
                    batch.clear()
            processed += 1

        if not dry and batch:
            Product.objects.bulk_update(batch, FIELDS)

        self.stdout.write("")
        self.stdout.write("Sample (name | old -> new):")
        for name, old, new in samples:
            self.stdout.write(f"  {name:<40} Rs {old} -> Rs {new}")

        verb = "Would reprice" if dry else "Repriced"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {changed} of {processed} product(s)."))

