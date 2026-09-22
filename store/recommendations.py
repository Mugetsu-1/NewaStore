"""Content-based product recommendation engine.

This is the "Recommendation Module" required by the CSC381 lab (Lab 4 /
report Chapter 7). It implements **content-based filtering**: given the
product a visitor is looking at, it recommends catalogue items that are
*similar in their own attributes* — no purchase history needed, so it works
from day one and sidesteps the collaborative-filtering cold-start problem on
a sparse user-item matrix.

Algorithm (weighted similarity, cosine-style aggregate over 4 features)
----------------------------------------------------------------------
Each product is a feature vector; similarity of a candidate C to the base
product B is the weighted sum of four per-feature scores, each in [0, 1]:

    genre overlap   (weight 0.45)  Jaccard overlap of the tag/genre sets
    category match  (weight 0.25)  1.0 same category · 0.5 sibling · 0 else
    rating proximity(weight 0.15)  1 - |metaB - metaC| / 100
    price proximity (weight 0.15)  1 - |ln(1+pB) - ln(1+pC)| / ln(1+CAP)

The current product is excluded, inactive products are filtered out, results
are ranked descending, and the top-N (default 4) above a MIN_SCORE floor are
returned so an irrelevant filler is never shown. Every recommendation also
carries a human-readable ``rec_reason`` ("Also Action, Co-op") — content-based
scores are explainable, which the naive "same category" query never was.

For games the genres/tags are the dominant signal (hence the 0.45 weight),
which is the domain-appropriate analogue of "material" in the reference
handicraft report; the four-feature structure and the 0.35 floor are kept so
the module maps cleanly onto the report for viva defence.
"""

import math

from django.core.cache import cache
from django.db.models import Count, Q

from .models import Product

# --- Tunables (kept as module constants so they are easy to cite / defend) ---
W_GENRE = 0.45      # tag/genre Jaccard overlap — strongest signal for games
W_CATEGORY = 0.25   # same category / sibling category
W_RATING = 0.15     # metacritic proximity
W_PRICE = 0.15      # price-band proximity

MIN_SCORE = 0.35    # similarity floor: below this we do not recommend
DEFAULT_LIMIT = 4   # size of the "You may also like" strip
CANDIDATE_POOL = 80 # cap on rows scored in Python, pre-ranked in the DB
PRICE_CAP = 30000   # NPR reference span for the log price-distance scale
CACHE_TTL = 60 * 60 # recommendations are stable; cache the ranking for 1h
CACHE_VERSION = "v1"


def _price_proximity(a, b):
    """1.0 for identical prices, decaying with log-distance; robust to free games."""
    la, lb = math.log1p(max(0, a)), math.log1p(max(0, b))
    scale = math.log1p(PRICE_CAP)
    return max(0.0, 1.0 - abs(la - lb) / scale)


def _rating_proximity(a, b):
    """Metacritic proximity in [0, 1]; 0 when either score is missing."""
    if not a or not b:
        return 0.0
    return max(0.0, 1.0 - abs(a - b) / 100.0)


def _category_score(base, cand):
    """1.0 same category · 0.5 sibling (shared parent) · 0 otherwise."""
    if base.category_id and base.category_id == cand.category_id:
        return 1.0
    bp = getattr(base.category, "parent_id", None) if base.category_id else None
    cp = getattr(cand.category, "parent_id", None) if cand.category_id else None
    if bp and cp and bp == cp:
        return 0.5
    return 0.0


def _jaccard(a, b):
    """|A ∩ B| / |A ∪ B| for two sets; 0 when both are empty."""
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _reason(base_tag_names, shared_tag_names, cat_score, base):
    """Short, human-readable explanation for why this item is shown."""
    if shared_tag_names:
        shown = list(shared_tag_names)[:2]
        return "Also " + ", ".join(shown)
    if cat_score >= 1.0 and base.category_id:
        return f"More {base.category.name}"
    if cat_score >= 0.5:
        return "Similar category"
    return "You may also like"


def recommend_for_product(product, limit=DEFAULT_LIMIT):
    """Return up to ``limit`` active products similar to ``product``.

    Each returned Product has two attributes attached for the template:
    ``rec_score`` (float, 0-1) and ``rec_reason`` (str). Falls back to popular
    active products if nothing clears the similarity floor, so the strip is
    never empty on a fresh or oddly-tagged catalogue.
    """
    ck = f"rec:{CACHE_VERSION}:{product.pk}:{limit}"
    cached = cache.get(ck)
    if cached is not None:
        return _hydrate(cached, exclude_pk=product.pk, limit=limit)

    base_tag_ids = set(product.tags.values_list("id", flat=True))
    base_tag_names = set(product.tags.values_list("name", flat=True))

    # Narrow the pool in the DB first: only items that share the category or at
    # least one genre are plausible matches. Pre-rank by shared-tag count so the
    # Python scoring never sees more than CANDIDATE_POOL rows even on a huge
    # catalogue — this keeps the product page fast with thousands of games.
    pool = (
        Product.objects.filter(is_active=True)
        .exclude(pk=product.pk)
        .filter(Q(category_id=product.category_id) | Q(tags__in=base_tag_ids))
        .annotate(_shared=Count("tags", filter=Q(tags__in=base_tag_ids), distinct=True))
        .order_by("-_shared", "-metacritic_score", "-is_featured")
        .distinct()[:CANDIDATE_POOL]
        .prefetch_related("tags")
        .select_related("category")
    )

    scored = []
    base_price = float(product.current_price or 0)
    for cand in pool:
        cand_tag_names = set(t.name for t in cand.tags.all())
        shared = base_tag_names & cand_tag_names
        genre = _jaccard(base_tag_names, cand_tag_names)
        cat = _category_score(product, cand)
        rating = _rating_proximity(product.metacritic_score, cand.metacritic_score)
        price = _price_proximity(base_price, float(cand.current_price or 0))

        score = W_GENRE * genre + W_CATEGORY * cat + W_RATING * rating + W_PRICE * price
        if score >= MIN_SCORE:
            scored.append((round(score, 4), cand.pk,
                           _reason(base_tag_names, shared, cat, product)))

    scored.sort(key=lambda r: r[0], reverse=True)
    ranking = [(pk, reason, s) for s, pk, reason in scored[:limit]]

    if not ranking:
        # Cold start / thinly-tagged item: fall back to popular active products.
        fb = (Product.objects.filter(is_active=True)
              .exclude(pk=product.pk)
              .order_by("-is_featured", "-metacritic_score", "-created_at")
              .values_list("pk", flat=True)[:limit])
        ranking = [(pk, "Popular right now", 0.0) for pk in fb]

    cache.set(ck, ranking, CACHE_TTL)
    return _hydrate(ranking, exclude_pk=product.pk, limit=limit)


def _hydrate(ranking, exclude_pk, limit):
    """Fetch fresh Product rows for a cached ranking, preserving order.

    We cache only (pk, reason, score) — never the objects — so price, stock and
    artwork stay live while the expensive similarity ranking is reused.
    """
    order = [r[0] for r in ranking if r[0] != exclude_pk][:limit]
    if not order:
        return []
    by_pk = {
        p.pk: p
        for p in Product.objects.filter(pk__in=order, is_active=True)
        .select_related("category")
        .prefetch_related("images", "tags")
    }
    meta = {pk: (reason, score) for pk, reason, score in ranking}
    out = []
    for pk in order:
        p = by_pk.get(pk)
        if not p:
            continue
        p.rec_reason, p.rec_score = meta.get(pk, ("", 0.0))
        out.append(p)
    return out
