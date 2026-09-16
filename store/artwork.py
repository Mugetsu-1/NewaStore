"""Artwork pipeline: fetch-once, convert to WebP, store locally.

Design goal (the permanent fix for hotlink rot): every product image is
downloaded from its source CDN exactly once, converted into a lightweight WebP
card thumbnail, and stored under MEDIA_ROOT. Listing pages then serve our own
bytes - no more Steam 403/404s, User-Agent blocks or rate limits, and no
per-request dependency on a third-party CDN.

Storage is intentionally abstracted through :func:`save_thumbnail`: today it
writes into Django's default file storage (local MEDIA_ROOT), and moving to an
object store (Cloudflare R2 / S3) later means swapping only that function for
an S3 client call - every command, view and template stays unchanged.
"""
import io
import logging
import time

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image

logger = logging.getLogger(__name__)

STEAM_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
STEAM_APPDETAILS = "https://store.steampowered.com/api/appdetails"
APPDETAILS_MIN_INTERVAL = 1.0  # seconds between calls - stay polite
_last_appdetails_call = 0.0


class ArtworkError(Exception):
    """Raised when artwork cannot be fetched or processed."""


# --------------------------------------------------------------- downloading

def download_bytes(url, timeout=15):
    """Download an image; return raw bytes or raise ArtworkError.

    Anything that is not an actual image response (403, 404, HTML error page,
    empty body) raises instead of returning, so callers can never store a
    URL/file that would render as a broken image.
    """
    try:
        r = requests.get(url, headers=STEAM_UA, timeout=timeout, stream=True,
                         allow_redirects=True)
        code = r.status_code
        ctype = r.headers.get("Content-Type", "")
        if code not in (200, 206) or not ctype.startswith("image"):
            raise ArtworkError(f"HTTP {code} ({ctype or 'no content-type'})")
        data = r.raw.read(8 * 1024 * 1024, decode_content=True)  # hard 8MB cap
        r.close()
        if not data:
            raise ArtworkError("empty body")
        return data
    except requests.RequestException as exc:
        raise ArtworkError(f"{type(exc).__name__}: {exc}") from exc


def to_webp(data, max_width=None, quality=None):
    """Convert image bytes to a WebP thumbnail. Returns (webp_bytes, width, height)."""
    max_width = max_width or getattr(settings, "ARTWORK_THUMB_WIDTH", 480)
    quality = quality or getattr(settings, "ARTWORK_WEBP_QUALITY", 82)
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            if im.width > max_width:
                height = round(im.height * max_width / im.width)
                im = im.resize((max_width, height), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, format="WEBP", quality=quality, method=5)
            return out.getvalue(), im.width, im.height
    except Exception as exc:  # Pillow raises a family of errors
        raise ArtworkError(f"WebP conversion failed: {exc}") from exc


def save_thumbnail(img_row, webp_bytes):
    """Persist thumbnail bytes through Django's storage backend.

    This is the single seam to replace when moving to Cloudflare R2 / S3:
    implement the same signature with an S3 client (bucket + key) and the
    whole pipeline - commands, ensure_ready, templates - keeps working.
    """
    name = f"{img_row.product_id}-{img_row.pk}.webp"
    img_row.thumbnail.save(name, ContentFile(webp_bytes), save=True)


def materialize_row(img_row, timeout=15):
    """Fetch-once for one ProductImage. Returns True when a thumbnail was made.

    Idempotent: rows that already carry a thumbnail are skipped by callers.
    """
    if not img_row.external_url:
        return False
    raw = download_bytes(img_row.external_url, timeout=timeout)
    webp, _w, _h = to_webp(raw)
    save_thumbnail(img_row, webp)
    return True


# ----------------------------------------------------------------- resolvers

def steam_header_image(appid):
    """Native artwork URL straight from Steam's appdetails API (rate-limited)."""
    global _last_appdetails_call
    wait = APPDETAILS_MIN_INTERVAL - (time.monotonic() - _last_appdetails_call)
    if wait > 0:
        time.sleep(wait)
    _last_appdetails_call = time.monotonic()
    try:
        r = requests.get(STEAM_APPDETAILS, params={"appids": appid, "l": "english"},
                         headers=STEAM_UA, timeout=15)
    except requests.RequestException as exc:
        raise ArtworkError(f"{type(exc).__name__}: {exc}") from exc
    if r.status_code != 200:
        raise ArtworkError(f"HTTP {r.status_code}")
    entry = (r.json() or {}).get(str(appid)) or {}
    if not entry.get("success"):
        raise ArtworkError("appdetails success=false (delisted or wrong appid)")
    data = entry.get("data") or {}
    for key in ("header_image", "capsule_imagev5", "capsule_image"):
        url = data.get(key)
        if url:
            return url
    raise ArtworkError("no image fields in appdetails payload")


def cheapshark_search_thumb(name):
    """CheapShark title search -> deal thumbnail (no credentials needed)."""
    try:
        r = requests.get("https://www.cheapshark.com/api/1.0/games",
                         params={"title": name, "limit": 3},
                         headers={"User-Agent": "NewaStore/1.0 (portfolio demo)"},
                         timeout=15)
    except requests.RequestException as exc:
        raise ArtworkError(f"{type(exc).__name__}: {exc}") from exc
    if r.status_code != 200:
        raise ArtworkError(f"HTTP {r.status_code}")
    games = r.json().get("games") or []
    if not games:
        raise ArtworkError("no title match")
    wanted = name.strip().lower()
    best = next((g for g in games
                 if (g.get("name") or "").strip().lower() == wanted), games[0])
    thumb = best.get("thumb") or ""
    if not thumb:
        raise ArtworkError("match has no thumb")
    return thumb


def igdb_cover_url(name, client_id, client_secret):
    """IGDB (Twitch) cover art. Requires TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET."""
    if not client_id or not client_secret:
        raise ArtworkError("IGDB not configured (TWITCH_CLIENT_ID/SECRET missing)")
    auth = requests.post("https://id.twitch.tv/oauth2/token", params={
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "client_credentials",
    }, timeout=15)
    token = auth.json().get("access_token")
    if not token:
        raise ArtworkError("twitch oauth failed")
    resp = requests.post(
        "https://api.igdb.com/v4/covers",
        data='fields image_id; search "{}"; limit 1;'.format(name.replace('"', "")),
        headers={"Client-ID": client_id, "Authorization": f"Bearer {token}"},
        timeout=15)
    covers = resp.json() if resp.status_code == 200 else []
    if not covers or not covers[0].get("image_id"):
        raise ArtworkError("no IGDB cover")
    image_id = covers[0]["image_id"]
    return f"https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg"