# Newa Store — Full E-Commerce Platform

A complete e-commerce web application built with **Django + PostgreSQL**. The
catalog is seeded with **real game data** (titles, prices, ratings, release
dates, cover art) fetched live from the [CheapShark](https://www.cheapshark.com/api/1.0)
and [SteamSpy](https://steamspy.com/) APIs — no API keys required. It ships
with a modern responsive storefront, guest + account checkout, multiple
payment gateways, order management, reviews, coupons, wishlists, SEO, a
customized admin dashboard, and a test suite.

> **Development status:** This project is configured for local development.
> Review the production checklist and replace every development credential
> before deploying it publicly.

## Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Accounts & first login](#accounts--first-login)
- [Useful commands](#useful-commands)
- [Project structure](#project-structure)
- [Model overview](#model-overview)
- [Email setup](#email-setup-gmail-smtp)
- [Configuration](#configuration)
- [Security before production](#security-before-production)
- [Going to production](#going-to-production-checklist)

## Data sources

| Source | What it provides | Used by |
|--------|------------------|---------|
| **CheapShark** (no key) | Active deals across 14 stores: real USD prices, Metacritic scores, Steam ratings, thumbnails | `ensure_ready`/`audit_product_images` + live search import |
| **Steam appdetails** (no key) | Genres, real descriptions, developers, publishers, release dates, HD screenshots | Seeder enrichment (featured games) |
| **SteamSpy** (no key) | The full Steam catalog (~60-80k games): names, prices in cents, developers | `manage.py import_steamspy` |

CheapShark's `page` parameter is broken, so the importer enumerates the
catalog by **price band x sort x store** combos instead of paging. Artwork is
initially sourced from the Steam CDN, then materialized as local WebP files
under `MEDIA_ROOT` so storefront pages do not depend on third-party image
requests. Rows confirmed to have no artwork are marked unavailable and are
not retried on every startup.

---

## Features

### Storefront
- **Home page** — Steam/Epic-style hero carousel with real game art, genre browse chips, featured & recommended, special deals, new releases, best sellers
- **Shop / catalog** — sidebar filters (genre, price, deals, search), sorting, pagination
- **Category & tag/genre pages**
- **Product detail** — 16:9 image gallery with HD screenshots, genre chips, Steam-style discount pricing, real release dates, tabs (description, specs, reviews), related games
- **Search** — live AJAX suggestions + zero-result live import (searches CheapShark and imports matches instantly)
- **JSON API** — `/api/search/`, `/api/browse/`, `/api/games/<slug>/`, `/api/genres/`, `/api/contact/`, `/api/health/` (Django REST Framework)
- **Recommendations** — explainable content-based recommendations using genre overlap, category, rating, and price similarity
- **AJAX shop** — filter/sort without page reloads, infinite scroll pagination
- **Wishlist** — add/remove, move to cart
- **Reviews & ratings** — star ratings, verified-purchase badges, helpful votes
- **Responsive design** — dark/light theme toggle, mobile navigation
- **Digital delivery** — games are delivered as license keys by email; there is no physical shipping
- **Static pages** — About, Contact, FAQ, Privacy, Terms, Refund Policy

### Cart & Checkout
- Persistent cart that works for **guests (session)** and **logged-in users**, merged on login
- Quantity updates, cart drawer
- **Coupons** — percentage and fixed-amount types with usage limits
- **Guest checkout** and account checkout
- Billing details collected at checkout and stored on the order for invoices
- Automatic **tax calculation**
- Payment methods: **eSewa and Nay Bank Transfer**. eSewa (ePay v2) uses its **real sandbox API** with server-side HMAC-SHA256 signature verification; Nay Bank is an offline bank transfer settled manually. No real money moves in sandbox mode.
- Order confirmation + status emails

### Orders
- Order history with status filter
- Order detail with status timeline
- **PDF invoice** download (ReportLab with HTML fallback)
- Cancel / reorder
- Admin bulk status actions and status history audit trail

### Accounts
- Registration, login (username **or** email), logout
- Password reset flow
- Dashboard with order/wishlist stats
- Profile editing, password change

### Admin (Jazzmin)
- Rich, dark-themed dashboard
- Full CRUD for products, categories, tags, orders, coupons, reviews, settings
- Inline product images & variants
- Bulk order status actions, review moderation, subscriber management

### Platform
- SEO: meta tags, Open Graph, `sitemap.xml`, `robots.txt`
- Custom 404 / 500 pages, maintenance mode
- Newsletter subscriptions, contact messages
- Signals to auto-create wishlists, track order status changes
- Optional Google Analytics 4 tracking configured from Site Settings
- Byte-compiled-ready, environment-variable configuration
- Automated health checks and checkout verification scripts

---

## Quick Start

> Requires **Python 3.10+** and **PostgreSQL 14+** (service running; see `.env.example`).

```bash
# 0. Create and activate a virtual environment
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
# source .venv/bin/activate

# 1. Create the database (one-time, as the postgres superuser):
#    CREATE USER newastore WITH PASSWORD '...';
#    CREATE DATABASE newastore OWNER newastore;
#    ALTER ROLE newastore CREATEDB;   -- for Django's test runner
#    ...then put the credentials in .env (see .env example below)

# 2. Configure environment variables
copy .env.example .env       # Windows
# cp .env.example .env       # macOS/Linux
# Edit .env and set database, email, and payment values as needed.

# 3. Install dependencies
python -m pip install -r requirements.txt

# 4. Run it. That's all - the server bootstraps itself on startup.
python manage.py runserver
```

Then open <http://127.0.0.1:8000/>.

### What happens on startup

`runserver` **bootstraps itself in the background**: it applies any pending
migrations, imports the full SteamSpy catalog when the database is empty
(resumable, ~15–20 min the very first time), repairs missing game artwork,
materializes local WebP thumbnails, and sweeps stored artwork URLs for dead
links. Every step is incremental and rate-limited, so a normal restart
finishes its bootstrap in milliseconds. The server starts listening
immediately; the first page load on a brand-new database may just need a
moment.

```bash
python manage.py runserver --skip-bootstrap    # vanilla runserver (or set NEWASTORE_SKIP_BOOTSTRAP=1)
python manage.py runserver --force-bootstrap   # run repair/materialize/audit now
python manage.py runserver --bootstrap-workers 32
python manage.py ensure_ready --force          # same, outside of runserver
python manage.py ensure_ready --skip-materialize
python manage.py materialize_images --workers 16
```

The `.env` file is intentionally gitignored. Start from `.env.example` and
never commit real passwords, API keys, SMTP app passwords, or payment secrets:

```
DB_NAME=newastore
DB_USER=newastore
DB_PASSWORD=...
DB_HOST=localhost
DB_PORT=5433
```

### Accounts & first login

The dev server seeds two accounts on startup from your `.env` (see
`.env.example`), and resets their passwords to match on every boot, so login
always agrees with `.env`:

| Role     | URL         | Username source              | Password source                |
|----------|-------------|------------------------------|--------------------------------|
| Admin    | `/admin/`   | `DJANGO_SUPERUSER_USERNAME`  | `DJANGO_SUPERUSER_PASSWORD`    |
| Customer | `/login/`   | `DEMO_USER_USERNAME`         | `DEMO_USER_PASSWORD`           |

Set these in `.env` before first run (defaults are `admin` / `demo`). The demo
customer is skipped if `DEMO_USER_PASSWORD` is left blank. Change every
credential before deploying to production.

---

## Useful commands

```bash
python manage.py test                          # run Django's test suite
python test_checkout.py                         # exercise the checkout flow against the configured database
python manage.py createsuperuser               # create your own admin
python manage.py collectstatic                 # gather static files (production)

# --- data pipeline (all of this also runs automatically on runserver) ---
python manage.py ensure_ready                  # migrations + import + repair + audit
python manage.py ensure_ready --force          # run repair/audit now
python manage.py import_steamspy               # full Steam catalog import (resumable)
python manage.py import_steamspy --pages 5     # import 5 pages then stop
python manage.py repair_missing_images         # fill in placeholder artwork
python manage.py materialize_images            # download and store local WebP thumbnails
python manage.py audit_product_images          # probe stored URLs for dead links
python manage.py reprice_catalog --dry-run     # preview tiered NPR catalog pricing
python manage.py reprice_catalog               # apply tiered pricing and feature top AAA titles
python verify_all.py                           # end-to-end health check against the configured database
```

---

## Project structure

```
newastore/
├── manage.py
├── dump_status.py             # print database and catalog status
├── test_checkout.py          # checkout-flow verification script
├── verify_all.py             # end-to-end route, checkout, admin, and configuration checks
├── verify_images.py          # inspect stored product artwork
├── verify_state.py           # inspect email and checkout state
├── LICENSE                   # project license
├── SECURITY.md               # security reporting guidance
├── requirements.txt
├── .env.example              # safe configuration template
├── media/                    # local uploaded images (gitignored)
├── newastore/                # project config
│   ├── settings.py           # env-var driven, Jazzmin, payments
│   └── urls.py               # admin, sitemap, media, error handlers
└── store/
    ├── models.py             # core models (products, orders, cart, reviews, coupons, etc.)
    ├── recommendations.py    # explainable content-based product ranking
    ├── artwork.py            # artwork repair, fetching, and WebP materialization
    ├── views.py              # catalog, cart, checkout, orders, account, pages
    ├── forms.py              # all forms & formsets
    ├── admin.py              # customized admin
    ├── cart.py               # CartManager (session + user merge)
    ├── utils.py              # tax, email, PDF invoice, helpers
    ├── context_processors.py # site settings, nav, cart
    ├── signals.py            # auto wishlist + order status history
    ├── sitemaps.py
    ├── urls.py
    ├── templatetags/
    │   └── store_extras.py   # currency, stars, query_replace, badges
    ├── static/
    │   ├── css/style.css     # full design system
    │   └── js/main.js        # AJAX cart, wishlist, drawer, tabs, theme
    └── templates/
        ├── base.html
        ├── store/            # storefront + account + orders + pages + errors
        ├── registration/     # login, register, password reset
        └── emails/           # transactional email templates
```

---

## Model overview

| Model | Purpose |
|-------|---------|
| `Category`, `Tag` | Product organization (nested categories) |
| `Product`, `ProductImage`, `ProductVariant` | Catalog with gallery, variants, inventory |
| `Review`, `ReviewImage` | Ratings & product reviews |
| `Cart`, `CartItem` | Session/user cart |
| `Wishlist`, `WishlistItem` | Saved products |
| `Coupon`, `CouponUsage` | Discounts with limits & conditions |
| `Order`, `OrderItem`, `OrderStatusHistory` | Orders with immutable line items & audit trail |
| `NewsletterSubscriber`, `ContactMessage` | Marketing & support |
| `SavedBillingDetail` | Reusable signed-in customer billing details |
| `SiteSettings` | Global store configuration (singleton) |

---

## Email setup (Gmail SMTP)

By default Django prints emails to the console. To send real emails:

1. Enable **2-Step Verification** on the sending Google account.
2. Generate an App Password at <https://myaccount.google.com/apppasswords> (app type *Mail*) - the 16-char code keeps its spaces.
3. Put it in `.env` (see `.env.example`), then verify end-to-end:

    python manage.py send_test_email --to you@gmail.com

Transactional flows that use it: welcome (on signup), password reset, order confirmation, payment receipt, order status, and admin contact notifications. Failed sends are logged instead of silently dropped. An admin action on Site Settings (Send test email) sends a verification email too.

## Security before production

- Set `DJANGO_DEBUG=False` and generate a unique, high-entropy
  `DJANGO_SECRET_KEY`.
- Replace all demo usernames and passwords, including the admin account.
- Use production credentials for PostgreSQL, SMTP, and the eSewa gateway. Do not
  place them in source control.
- Restrict `DJANGO_ALLOWED_HOSTS` and configure `SITE_BASE_URL` to the HTTPS
  hostname used by the deployment.
- Swap eSewa's RC sandbox product code/secret/URLs for live values before
  accepting real payments.
- Run migrations and `collectstatic` as part of deployment; do not use the
  development server in production.

## Configuration

Key settings can be overridden via environment variables:

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Secret key (set in production) |
| `DJANGO_DEBUG` | `False` in production |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated host list |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection |
| `DJANGO_EMAIL_BACKEND` | Email backend (defaults to console) |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | SMTP |
| `ESEWA_PRODUCT_CODE`, `ESEWA_SECRET_KEY`, `ESEWA_FORM_URL`, `ESEWA_STATUS_URL` | eSewa ePay v2 (defaults to the public RC sandbox) |
| `SITE_BASE_URL` | Absolute base URL used in transactional emails |
| `USD_TO_NPR` | USD→NPR rate used by importers (default: 135) |
| `SEED_MAX_REQUESTS` | Max CheapShark requests in the sweep (default: 400) |
| `SEED_ENRICH` | Top games to Steam-enrich after the sweep (default: 150) |
| `ARTWORK_THUMB_WIDTH` | Width of generated local WebP thumbnails (default: 616) |
| `ARTWORK_WEBP_QUALITY` | WebP quality for generated thumbnails (default: 82) |

By default email is printed to the console, so order confirmation and password
reset emails can be viewed in the terminal during development.

`verify_all.py` and the checkout verification scripts use the configured
PostgreSQL database and may create temporary test records. Run them only
against a development or staging database.

Google Analytics 4 is optional. Set the Measurement ID in the Site Settings
admin form to enable page-view and recommendation-click tracking; no
third-party analytics script is loaded when the field is blank.

---

## Going to production (checklist)

1. Set `DJANGO_DEBUG=False` and a strong `DJANGO_SECRET_KEY`.
2. Configure `DJANGO_ALLOWED_HOSTS` (PostgreSQL is already the default database).
3. Configure SMTP email credentials.
4. Configure the live eSewa product code + secret and swap the RC sandbox URLs
   (`ESEWA_FORM_URL`, `ESEWA_STATUS_URL`) for production, then update the Nay Bank
   account details in `store/payments.py` (`BANK_TRANSFER_DETAILS`).
5. Add WhiteNoise (already in `requirements.txt`) or a reverse proxy for static/media.
6. Run `python manage.py collectstatic`.
7. Serve behind HTTPS (security settings auto-enable when `DEBUG=False`).
