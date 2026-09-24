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
import threading
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
APPDETAILS_MIN_INTERVAL = 1.0
_last_appdetails_call = 0.0
_appdetails_lock = threading.Lock()


class ArtworkError(Exception):
    """Raised when artwork cannot be fetched or processed."""


class ArtworkUnavailable(ArtworkError):
    """Raised when the source *definitively* has no art (e.g. appdetails
    success=false for a delisted app). Distinct from a transient failure (429,
    timeout, 5xx) so callers can permanently stop retrying the row instead of
    hammering a dead appid on every boot."""



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
        data = r.raw.read(8 * 1024 * 1024, decode_content=True)
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
    except Exception as exc:
        raise ArtworkError(f"WebP conversion failed: {exc}") from exc


def save_thumbnail(img_row, webp_bytes, save=True):
    """Persist thumbnail bytes through Django's storage backend.

    This is the single seam to replace when moving to Cloudflare R2 / S3:
    implement the same signature with an S3 client (bucket + key) and the
    whole pipeline - commands, ensure_ready, templates - keeps working.

    ``save=True`` writes the file *and* saves the model (single-row callers).
    ``save=False`` writes the file and sets the field, but leaves the DB write
    to the caller - used by the batch command, which does one ``update()`` on
    the main thread so worker threads never open a database connection.
    Returns the stored file name so a caller can persist it with ``update()``.
    """
    name = f"{img_row.product_id}-{img_row.pk}.webp"
    img_row.thumbnail.save(name, ContentFile(webp_bytes), save=save)
    return img_row.thumbnail.name


STEAM_CDN_HOSTS = (
    "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps",
    "https://cdn.cloudflare.steamstatic.com/steam/apps",
    "https://cdn.akamai.steamstatic.com/steam/apps",
)
STEAM_ART_FILES = ("header.jpg", "capsule_616x353.jpg", "capsule_231x87.jpg")


def steam_cdn_candidates(appid, stored_url=""):
    """Ordered, de-duplicated list of artwork URLs to try for one product.

    The stored URL is tried first (it works for most rows); when it is a dead
    Steam capsule, the header/small-capsule variants on each mirror are tried
    next, so an appid almost always yields at least one live image.
    """
    urls = []
    if stored_url:
        urls.append(stored_url)
    if appid:
        for fname in STEAM_ART_FILES:
            for host in STEAM_CDN_HOSTS:
                urls.append(f"{host}/{appid}/{fname}")
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_thumbnail(external_url, appid=None, timeout=15):
    """Download + convert one image to WebP. **No database access.**

    Pure network + CPU: tries the stored URL, then deterministic Steam CDN
    fallbacks derived from the appid, and finally the appdetails API (which
    knows the modern hashed asset path that the deterministic guesses miss for
    recent/relisted apps). Returns ``(webp_bytes, winning_url)`` for the first
    candidate that resolves. Because it never touches the ORM it is safe to call
    from many worker threads at once - each thread would otherwise open its own
    (thread-local) Postgres connection and exhaust the server's connection
    slots. The caller persists the result on one thread.

    Returns ``None`` when there is nothing to fetch (no URL and no appid);
    raises :class:`ArtworkError` when every candidate fails.
    """
    candidates = steam_cdn_candidates(appid, external_url)
    if not candidates and not appid:
        return None
    last_err = None
    for url in candidates:
        try:
            raw = download_bytes(url, timeout=timeout)
        except ArtworkError as exc:
            last_err = exc
            continue
        webp, _w, _h = to_webp(raw)
        return webp, url
    if appid:
        try:
            resolved = steam_header_image(appid)
            raw = download_bytes(resolved, timeout=timeout)
            webp, _w, _h = to_webp(raw)
            return webp, resolved
        except ArtworkUnavailable:
            raise
        except ArtworkError as exc:
            last_err = exc
    if last_err is None:
        return None
    raise last_err


def steam_header_image(appid):
    """Native artwork URL straight from Steam's appdetails API (rate-limited).

    Thread-safe: the 1-req/sec gate is held under a lock so concurrent
    materialize workers space their calls out politely instead of racing the
    shared timestamp. The HTTP request itself runs outside the lock, so one slow
    response does not stall the next worker's turn.
    """
    global _last_appdetails_call
    with _appdetails_lock:
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
    entry = (r.json() or {}).get(str(appid))
    if entry is None:
        raise ArtworkError("appdetails returned no entry (rate-limited?)")
    if not entry.get("success"):
        raise ArtworkUnavailable("appdetails success=false (delisted or wrong appid)")
    data = entry.get("data") or {}
    for key in ("header_image", "capsule_imagev5", "capsule_image"):
        url = data.get(key)
        if url:
            return url
    raise ArtworkUnavailable("no image fields in appdetails payload")