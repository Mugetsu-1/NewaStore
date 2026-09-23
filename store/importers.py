"""Live import helpers for CheapShark, Steam and SteamSpy data.

Used by:
    - populate_db.py              (CheapShark enumeration sweep + upserts)
    - manage.py import_steamspy   (full Steam catalog background import)
    - store.views.search          (zero-result live import)

All network access is rate-limit aware: CheapShark bans hammering IPs for
long stretches, Steam returns 429s, SteamSpy asks for ~4 req/min.
"""
import os
import re
import time
import threading
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP

import requests
import uuid

from django.utils import timezone as dj_tz
from django.utils.text import slugify

from .models import Product, ProductImage, Tag, Category

CS_API = 'https://www.cheapshark.com/api/1.0'
STEAM_APPDETAILS = 'https://store.steampowered.com/api/appdetails'
STEAMSPY_API = 'https://steamspy.com/api.php'

USER_AGENT = 'NewaStore Seeder/1.0 (educational demo project)'
REQUEST_TIMEOUT = 25

USD_TO_NPR = Decimal(os.environ.get('USD_TO_NPR', '135'))

BULK_BATCH = 500

SKIP_TITLE_PATTERNS = [
    re.compile(r'\bDLC\b', re.I),
    re.compile(r'Soundtrack', re.I),
    re.compile(r'Season Pass', re.I),
    re.compile(r'\bOST\b', re.I),
    re.compile(r'\bDemo\b(?!n)', re.I),
]

CS_STORES = ['1', '2', '3', '7', '11', '13', '15', '21', '23', '25', '27', '28', '30', '35']

PRICE_BANDS = [
    (0, 1), (1, 2), (2, 3), (3, 5), (5, 7), (7, 10),
    (10, 15), (15, 20), (20, 30), (30, 50), (50, 60), (60, 100000),
]


def usd_to_npr(usd):
    npr = (Decimal(usd) * USD_TO_NPR).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return (npr // 10) * 10


def normalize_title(title):
    return re.sub(r'[^a-z0-9]', '', (title or '').lower())


def is_game(title):
    return not any(p.search(title or '') for p in SKIP_TITLE_PATTERNS)


def parse_ts(ts):
    try:
        ts = int(ts)
        if ts > 0:
            return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
    except (TypeError, ValueError):
        pass
    return None


def get_or_create_tag(name):
    """Slug-safe tag lookup: different spellings can slugify identically
    (e.g. 'Free To Play' vs 'Free to Play'), so match by slug as a fallback."""
    name = (name or '').strip()[:50]
    if not name:
        return None
    tag = Tag.objects.filter(name=name).first()
    if tag:
        return tag
    slug = slugify(name)
    if not slug:
        return None
    tag = Tag.objects.filter(slug=slug).first()
    if tag:
        return tag
    try:
        tag, _ = Tag.objects.get_or_create(name=name)
    except Exception:
        tag = Tag.objects.filter(slug=slug).first()
    return tag



class Api:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        self._last = {}
        self._lock = threading.Lock()

    def _throttle(self, host, min_interval):
        with self._lock:
            now = time.time()
            last = self._last.get(host, 0)
            wait = min_interval - (now - last)
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.time()

    def get_json(self, url, params=None, min_interval=0.75, retries=2, ok_429=True):
        host = re.sub(r'^https?://', '', url).split('/')[0]
        for attempt in range(1, retries + 1):
            self._throttle(host, min_interval)
            try:
                r = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
                if r.status_code == 429:
                    if not ok_429:
                        return 'RATE_LIMITED'
                    time.sleep(15 * attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException:
                if attempt == retries:
                    return None
                time.sleep(attempt * 2)
        return None

    def get_image(self, url):
        try:
            self._throttle(url.split('/')[2], 0.05)
            r = self.session.get(url, timeout=30)
            if r.ok and r.headers.get('Content-Type', '').startswith('image/'):
                return r.content
        except requests.RequestException:
            pass
        return None


api = Api()


def wait_for_cheapshark(max_minutes=45):
    """Block until CheapShark answers (survives temporary rate-limit bans)."""
    deadline = time.time() + max_minutes * 60
    attempt = 0
    while time.time() < deadline:
        data = api.get_json(f'{CS_API}/deals', {'pageSize': 1},
                            min_interval=5, retries=1, ok_429=False)
        if data == 'RATE_LIMITED':
            attempt += 1
            wait = min(60 * attempt, 300)
            print(f'  CheapShark rate-limiting; waiting {wait}s (attempt {attempt})...')
            time.sleep(wait)
            continue
        if data is not None:
            if attempt:
                print('  CheapShark reachable again.')
            return True
        time.sleep(15)
    return False



def steam_capsule_url(appid):
    return (f'https://shared.fastly.steamstatic.com/store_item_assets/steam/'
            f'apps/{appid}/capsule_616x353.jpg')


def product_external_url(appid, game_id=None):
    if appid:
        return f'https://store.steampowered.com/app/{appid}/'
    if game_id:
        return f'https://www.cheapshark.com/game/{game_id}/'
    return ''



def _games_category():
    category, _ = Category.objects.get_or_create(
        name='Games',
        defaults=dict(description='Digital PC game keys — live price data', sort_order=0))
    return category


def upsert_games(games, data_source, update_existing=True, on_progress=None):
    """Bulk-create/update Products from normalized game dicts.

    Each game dict: name, price_usd, discount_usd (or None), appid (or None),
    game_id (CheapShark id or None), metacritic (int or None), release (dt or None),
    thumb (url or None), description/short_description (optional),
    genres (list of str, optional).

    Upsert key: steam_app_id when present, otherwise normalized title.
    Returns (created_count, updated_count).
    """
    games = [g for g in games if g.get('name') and is_game(g['name'])
             and len(normalize_title(g['name'])) >= 2]
    if not games:
        return 0, 0

    category = _games_category()

    appids = [g['appid'] for g in games if g.get('appid')]
    existing_by_appid = {}
    if appids:
        existing_by_appid = dict(
            Product.objects.filter(steam_app_id__in=appids)
            .values_list('steam_app_id', 'id'))

    need_title_map = any(not g.get('appid') for g in games)
    existing_by_title = {}
    if need_title_map:
        for pid, name in Product.objects.values_list('id', 'name'):
            existing_by_title.setdefault(normalize_title(name), pid)

    existing_slugs = set(Product.objects.values_list('slug', flat=True))
    existing_skus = set(Product.objects.values_list('sku', flat=True))

    to_create = []
    updates = {}
    update_fields = set()

    for g in games:
        appid = g.get('appid')
        game_id = g.get('game_id')
        name = g['name'][:200]

        pid = existing_by_appid.get(appid) if appid else existing_by_title.get(normalize_title(name))
        if pid:
            if not update_existing:
                continue
            p = updates.get(pid) or Product(pk=pid)
            changed = False
            if g.get('price_usd') is not None:
                new_price = usd_to_npr(g['price_usd'])
                if p.price != new_price:
                    p.price = new_price
                    update_fields |= {'price'}
                    changed = True
            new_disc = usd_to_npr(g['discount_usd']) if g.get('discount_usd') else None
            if g.get('discount_usd') is not None and p.discount_price != new_disc:
                p.discount_price = new_disc
                update_fields |= {'discount_price'}
                changed = True
            if g.get('metacritic') is not None and p.metacritic_score != g['metacritic']:
                p.metacritic_score = g['metacritic']
                update_fields |= {'metacritic_score'}
                changed = True
            ext = product_external_url(appid, game_id)
            if ext and not p.external_url:
                p.external_url = ext
                update_fields |= {'external_url'}
                changed = True
            if changed:
                updates[pid] = p
            continue

        base_slug = slugify(name)[:180] or f'game-{appid or game_id or uuid.uuid4().hex[:8]}'
        slug = base_slug
        if slug in existing_slugs:
            slug = f'{base_slug[:170]}-{appid or game_id or uuid.uuid4().hex[:6]}'
        while slug in existing_slugs:
            slug = f'{base_slug[:165]}-{uuid.uuid4().hex[:8]}'
        existing_slugs.add(slug)

        sku = f'SS-{appid}' if appid else f'CS-{game_id}' if game_id else f'SKU-{uuid.uuid4().hex[:8].upper()}'
        if sku in existing_skus:
            sku = f'{sku}-{uuid.uuid4().hex[:4].upper()}'
        existing_skus.add(sku)

        price_usd = g.get('price_usd') or Decimal('0')
        short = (g.get('short_description') or f'Digital PC key for {name}.')[:500]
        description = g.get('description') or (
            f'{short}\n\nKey details:\n'
            f'- Format: Digital download key (no shipping required)\n\n'
            f'Your key is delivered by email immediately after checkout.')
        release = g.get('release') or dj_tz.now()

        to_create.append(Product(
            name=name,
            slug=slug,
            sku=sku,
            description=description,
            short_description=short,
            price=usd_to_npr(price_usd),
            discount_price=usd_to_npr(g['discount_usd']) if g.get('discount_usd') else None,
            cost_price=usd_to_npr(price_usd * Decimal('0.7')) if price_usd else None,
            category=category,
            is_digital=True,
            track_inventory=False,
            steam_app_id=appid,
            external_url=product_external_url(appid, game_id),
            metacritic_score=g.get('metacritic'),
            data_source=data_source,
            meta_title=name[:60],
            meta_description=short[:160],
            published_at=release,
        ))

    created = 0
    for i in range(0, len(to_create), BULK_BATCH):
        batch = to_create[i:i + BULK_BATCH]
        Product.objects.bulk_create(batch, batch_size=BULK_BATCH)
        created += len(batch)
        if on_progress:
            on_progress(created)

    if updates and update_fields:
        rows = list(updates.values())
        for i in range(0, len(rows), BULK_BATCH):
            Product.objects.bulk_update(rows[i:i + BULK_BATCH],
                                        fields=list(update_fields), batch_size=BULK_BATCH)
    updated = len(updates)

    if to_create:
        thumb_by_name = {g['name'][:200]: g.get('thumb') for g in games if g.get('thumb')}
        images = []
        for p in to_create:
            url = steam_capsule_url(p.steam_app_id) if p.steam_app_id else None
            if not url:
                url = thumb_by_name.get(p.name)
            if url:
                images.append(ProductImage(product=p, external_url=url,
                                           alt_text=p.name, is_primary=True, sort_order=0))
        for i in range(0, len(images), BULK_BATCH):
            ProductImage.objects.bulk_create(images[i:i + BULK_BATCH], batch_size=BULK_BATCH)

    tag_pairs = []
    games_with_genres = [g for g in games if g.get('genres')]
    if games_with_genres:
        tag_ids = {}
        for g in games_with_genres:
            for genre in g['genres'][:4]:
                genre = (genre or '').strip()[:50]
                if not genre:
                    continue
                if genre not in tag_ids:
                    tag = get_or_create_tag(genre)
                    if tag:
                        tag_ids[genre] = tag.id
        all_by_appid = dict(Product.objects.filter(
            steam_app_id__in=[g['appid'] for g in games_with_genres if g.get('appid')]
        ).values_list('steam_app_id', 'id')) if any(g.get('appid') for g in games_with_genres) else {}
        through = Product.tags.through
        seen_pairs = set()
        for g in games_with_genres:
            pid = (all_by_appid.get(g['appid']) if g.get('appid')
                   else existing_by_title.get(normalize_title(g['name'])))
            if not pid:
                continue
            for genre in g['genres'][:4]:
                genre = genre.strip()[:50]
                tid = tag_ids.get(genre)
                if tid and (pid, tid) not in seen_pairs:
                    seen_pairs.add((pid, tid))
                    tag_pairs.append(through(product_id=pid, tag_id=tid))
        if tag_pairs:
            through.objects.bulk_create(tag_pairs, ignore_conflicts=True,
                                        batch_size=BULK_BATCH)

    return created, updated



def _deal_to_game(deal):
    normal = Decimal(str(deal.get('normalPrice') or '0'))
    sale = Decimal(str(deal.get('salePrice') or '0'))
    on_sale = deal.get('isOnSale') == '1' and 0 < sale < normal
    appid = deal.get('steamAppID')
    try:
        appid = int(appid) if appid else None
    except (TypeError, ValueError):
        appid = None
    mc = deal.get('metacriticScore')
    try:
        mc = int(mc) if mc else None
    except (TypeError, ValueError):
        mc = None
    game_id = deal.get('gameID')
    try:
        game_id = int(game_id) if game_id else None
    except (TypeError, ValueError):
        game_id = None
    return {
        'name': (deal.get('title') or '').strip(),
        'price_usd': normal if normal > 0 else sale,
        'discount_usd': sale if on_sale else None,
        'appid': appid,
        'game_id': game_id,
        'metacritic': mc,
        'release': parse_ts(deal.get('releaseDate')),
        'thumb': deal.get('thumb') or None,
    }


def sweep_deals(max_requests=400, on_progress=None):
    """Enumerate CheapShark's catalog via band x sort (x store) combos.

    CheapShark's `page` parameter is broken, so partitioning on other axes is
    the only way to enumerate the full catalog.
    """
    combos = []
    for band in PRICE_BANDS:
        for sort in ('Title', 'Metacritic', 'Deal Rating', 'Savings', 'Release'):
            combos.append({'lowerPrice': band[0], 'upperPrice': band[1], 'sortBy': sort})
    for band in PRICE_BANDS[:6]:
        for store in CS_STORES:
            combos.append({'lowerPrice': band[0], 'upperPrice': band[1],
                           'sortBy': 'Deal Rating', 'storeID': store})

    games = {}
    seen_keys = set()
    requests_used = 0
    consecutive_failures = 0

    for params in combos:
        if requests_used >= max_requests:
            break
        params = {**params, 'pageSize': 60}
        data = api.get_json(f'{CS_API}/deals', params)
        requests_used += 1
        if not isinstance(data, list):
            consecutive_failures += 1
            if consecutive_failures >= 3:
                print('  sweep: API unavailable — waiting for CheapShark to recover...')
                wait_for_cheapshark()
                consecutive_failures = 0
            continue
        consecutive_failures = 0
        added = 0
        for deal in data:
            game = _deal_to_game(deal)
            if not game['name']:
                continue
            key = f"appid:{game['appid']}" if game['appid'] else normalize_title(game['name'])
            if not key or key in seen_keys:
                continue
            if not game['price_usd'] or game['price_usd'] <= 0:
                continue
            seen_keys.add(key)
            games[key] = game
            added += 1
        if on_progress:
            on_progress(requests_used, len(games), added)

    return list(games.values())


def cheapshark_game_deals(game_id):
    """All store deals for one CheapShark game id (richest single-game fetch)."""
    data = api.get_json(f'{CS_API}/deals', {'gameID': game_id},
                        min_interval=0.75, retries=1, ok_429=False)
    if data == 'RATE_LIMITED':
        return None
    return data


def import_search_query(query):
    """Live-import games matching a search query. Returns count created."""
    from django.core.cache import cache

    norm = normalize_title(query)
    if len(norm) < 3:
        return 0

    miss_key = f'cs_search_miss:{norm}'
    if cache.get(miss_key):
        return 0
    if not cache.add('cs_search_lock', 1, 10):
        return 0

    matches = api.get_json(f'{CS_API}/games', {'title': query},
                           min_interval=0.75, retries=1, ok_429=False)
    if matches == 'RATE_LIMITED':
        cache.set(miss_key, 1, 60)
        return 0
    if not matches:
        cache.set(miss_key, 1, 300)
        return 0

    candidates = []
    for m in matches[:8]:
        appid = m.get('steamAppID')
        name = (m.get('external') or '').strip()
        if appid:
            exists = Product.objects.filter(steam_app_id=appid).exists()
        elif name:
            exists = Product.objects.filter(name__iexact=name).exists()
        else:
            exists = True
        if not exists:
            candidates.append(m)
    if not candidates:
        return 0

    games = []
    for m in candidates[:6]:
        deals = cheapshark_game_deals(m['gameID'])
        if not deals or not isinstance(deals, list):
            continue
        best = None
        for d in deals:
            try:
                if Decimal(str(d.get('normalPrice') or '0')) > 0:
                    if best is None or Decimal(str(d.get('savings') or 0)) > Decimal(str(best.get('savings') or 0)):
                        best = d
            except Exception:
                continue
        game = _deal_to_game(best or deals[0])
        if game['name']:
            games.append(game)

    created, _ = upsert_games(games, data_source='search_import')

    if created:
        enrich_with_steam(Product.objects.filter(
            data_source='search_import', steam_enriched=False,
            steam_app_id__isnull=False).order_by('-created_at')[:6])
    return created



def strip_html(html):
    text = re.sub(r'<br\s*/?>', '\n', html or '')
    text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = (text.replace('&nbsp;', ' ').replace('&amp;', '&')
                .replace('&quot;', '"').replace('&#39;', "'"))
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def parse_steam_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ('%d %b, %Y', '%b %d, %Y', '%d %B, %Y', '%B %d, %Y', '%Y'):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=dt_timezone.utc)
        except ValueError:
            continue
    return None


def enrich_with_steam(products, limit=200, delay=0.65):
    """Fill genres/description/release from the official Steam store API."""
    enriched = 0
    for p in products[:limit]:
        if not p.steam_app_id or p.steam_enriched:
            continue
        data = api.get_json(STEAM_APPDETAILS, {'appids': p.steam_app_id},
                            min_interval=delay, retries=2, ok_429=False)
        if data == 'RATE_LIMITED':
            print('    ! Steam throttling — pausing enrichment for 60s')
            time.sleep(60)
            continue
        if not data:
            continue
        payload = data.get(str(p.steam_app_id), {})
        if not (payload.get('success') and payload.get('data')):
            p.steam_enriched = True
            p.save(update_fields=['steam_enriched'])
            continue
        d = payload['data']

        genres = [g['description'] for g in d.get('genres', [])]
        short = (d.get('short_description') or '')[:500]
        about = strip_html(d.get('detailed_description') or d.get('about_the_game') or '')
        desc_parts = [short, ''] if short else []
        if about:
            desc_parts.append(about[:1200].rsplit(' ', 1)[0] + ('…' if len(about) > 1200 else ''))
            desc_parts.append('')
        desc_parts.append('Key details:')
        if genres:
            desc_parts.append(f'- Genres: {", ".join(genres)}')
        if d.get('developers'):
            desc_parts.append(f'- Developer: {", ".join(d["developers"])}')
        if d.get('publishers'):
            desc_parts.append(f'- Publisher: {", ".join(d["publishers"])}')
        desc_parts.append('- Format: Digital download key (no shipping required)')
        desc_parts.append('')
        desc_parts.append('Your key is delivered by email immediately after checkout.')

        p.description = '\n'.join(desc_parts)
        if short:
            p.short_description = short
        released = parse_steam_date((d.get('release_date') or {}).get('date'))
        if released:
            p.published_at = released
        p.steam_enriched = True
        p.save(update_fields=['description', 'short_description', 'published_at',
                              'steam_enriched'])

        through = Product.tags.through
        pairs = []
        for genre in genres[:4]:
            tag = get_or_create_tag(genre)
            if tag:
                pairs.append(through(product_id=p.id, tag_id=tag.id))
        if pairs:
            through.objects.bulk_create(pairs, ignore_conflicts=True)

        if p.is_featured:
            existing = p.images.count()
            shots = (d.get('screenshots') or [])[:2]
            imgs = [ProductImage(product=p, external_url=s.get('path_full'),
                                 alt_text=f'{p.name} screenshot {i + 1}',
                                 sort_order=existing + i)
                    for i, s in enumerate(shots) if s.get('path_full')]
            if imgs:
                ProductImage.objects.bulk_create(imgs)

        enriched += 1
    return enriched



def steamspy_page(page):
    """One page of SteamSpy's full catalog (~1000 apps)."""
    data = api.get_json(STEAMSPY_API, {'request': 'all', 'page': page},
                        min_interval=16, retries=3)
    if not isinstance(data, dict):
        return None
    return data


def steamspy_to_game(app):
    price_cents = Decimal(str(app.get('price') or '0'))
    initial_cents = Decimal(str(app.get('initialprice') or '0'))
    discount_pct = int(app.get('discount') or 0)
    price = price_cents / 100 if price_cents else Decimal('0')
    initial = initial_cents / 100 if initial_cents else price
    on_sale = discount_pct > 0 and 0 < price < initial
    return {
        'name': (app.get('name') or '').strip(),
        'price_usd': initial if initial > 0 else price,
        'discount_usd': price if on_sale else None,
        'appid': app.get('appid'),
        'game_id': None,
        'metacritic': None,
        'release': None,
        'thumb': steam_capsule_url(app.get('appid')) if app.get('appid') else None,
    }


def steamspy_genre_apps(genre):
    """Top ~1000 apps in a SteamSpy genre. Returns {appid: name}."""
    data = api.get_json(STEAMSPY_API, {'request': 'genre', 'genre': genre},
                        min_interval=16, retries=3)
    if not isinstance(data, dict):
        return {}
    return {int(appid): a.get('name', '') for appid, a in data.items()}
