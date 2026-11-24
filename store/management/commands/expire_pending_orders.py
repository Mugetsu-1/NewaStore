from django.core.management.base import BaseCommand

from store.utils import expire_stale_orders


class Command(BaseCommand):
    help = 'Cancel overdue unpaid orders and release inventory/coupon reservations.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        limit = max(1, min(options['limit'], 1000))
        total = 0
        while total < limit:
            count = expire_stale_orders(limit=min(100, limit - total))
            total += count
            if not count:
                break
        self.stdout.write(self.style.SUCCESS(f'Expired {total} unpaid order(s).'))
