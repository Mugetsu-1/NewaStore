"""Import the full Steam catalog from SteamSpy (background job).

SteamSpy serves the whole catalog in pages of ~1000 games and asks clients
to stay under ~4 requests/minute, so a full import (~60-80 pages) takes
around 15-20 minutes. Progress is stored in .steamspy_progress so the job
can be stopped and resumed at any time.

Usage:
    python manage.py import_steamspy                 # resume from marker
    python manage.py import_steamspy --pages 5       # only 5 pages this run
    python manage.py import_steamspy --start-page 0 --reset-progress
    python manage.py import_steamspy --genres-only   # just the genre tagging pass

Notes:
    - Existing products (e.g. CheapShark imports with real deal prices) are
      never overwritten; SteamSpy only fills in games that are missing.
    - Prices come from SteamSpy's snapshot (cents USD -> NPR at USD_TO_NPR).
"""
import json
import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from store.models import Product
from store.importers import (steamspy_page, steamspy_to_game, upsert_games,
                             steamspy_genre_apps)

PROGRESS_FILE = os.path.join(settings.BASE_DIR, '.steamspy_progress')

GENRES = [
    'Action', 'Adventure', 'Casual', 'Free to Play', 'Indie',
    'Massively Multiplayer', 'Racing', 'RPG', 'Simulation', 'Sports',
    'Strategy', 'Early Access',
]


class Command(BaseCommand):
    help = 'Import the full Steam catalog from SteamSpy (resumable background job).'

    def add_arguments(self, parser):
        parser.add_argument('--start-page', type=int, default=None,
                            help='Page to start from (default: resume from marker).')
        parser.add_argument('--pages', type=int, default=0,
                            help='Stop after N pages this run (0 = until the end).')
        parser.add_argument('--reset-progress', action='store_true',
                            help='Ignore and clear the saved progress marker.')
        parser.add_argument('--genres-only', action='store_true',
                            help='Only run the genre tagging pass.')
        parser.add_argument('--skip-genres', action='store_true',
                            help='Only import pages, skip the genre pass.')

    def handle(self, *args, **options):
        self.stdout.write('SteamSpy import — full Steam catalog')

        if options['reset_progress'] and os.path.exists(PROGRESS_FILE):
            os.remove(PROGRESS_FILE)
            self.stdout.write('  progress marker cleared.')

        if not options['genres_only']:
            self.run_pages(options)
        if not options['skip_genres']:
            self.run_genres()

        self.stdout.write(self.style.SUCCESS('SteamSpy import run finished.'))


    def read_marker(self):
        try:
            with open(PROGRESS_FILE) as fh:
                return json.load(fh).get('last_page', -1)
        except (OSError, ValueError):
            return -1

    def write_marker(self, page):
        with open(PROGRESS_FILE, 'w') as fh:
            json.dump({'last_page': page, 'updated': time.time()}, fh)

    def run_pages(self, options):
        start = options['start_page']
        if start is None:
            start = self.read_marker() + 1
            if start > 0:
                self.stdout.write(f'  resuming after page {start - 1}.')
        max_pages = options['pages'] or 0

        page = start
        pages_done = 0
        total_created = 0
        empty_streak = 0

        while True:
            if max_pages and pages_done >= max_pages:
                self.stdout.write(f'  page limit ({max_pages}) reached — stopping. '
                                  'Run again without --pages to continue.')
                break

            self.stdout.write(f'  fetching page {page}...')
            data = steamspy_page(page)

            if data is None:
                self.stdout.write(self.style.WARNING(
                    '  request failed — stopping; run again to resume.'))
                break
            if not data:
                self.stdout.write('  empty page — catalog fully imported!')
                break

            games = [steamspy_to_game(app) for app in data.values()]
            created, _ = upsert_games(games, data_source='steamspy',
                                      update_existing=False)
            total_created += created
            pages_done += 1
            self.stdout.write(f'    page {page}: {len(games)} apps, '
                              f'{created} new games (total new: {total_created})')
            self.write_marker(page)
            page += 1


    def run_genres(self):
        through = Product.tags.through
        from store.models import Tag

        appids_cache = None
        for genre in GENRES:
            self.stdout.write(f'  genre pass: {genre}...')
            apps = steamspy_genre_apps(genre)
            if not apps:
                self.stdout.write(self.style.WARNING(f'    no data for {genre}.'))
                continue
            tag, _ = Tag.objects.get_or_create(name=genre[:50])
            existing = dict(Product.objects.filter(
                steam_app_id__in=list(apps.keys())).values_list('steam_app_id', 'id'))
            pairs = [through(product_id=pid, tag_id=tag.id)
                     for pid in existing.values()]
            if pairs:
                through.objects.bulk_create(pairs, ignore_conflicts=True,
                                            batch_size=500)
            self.stdout.write(f'    {genre}: tagged {len(pairs)} games '
                              f'({len(apps) - len(existing)} not in catalog).')
