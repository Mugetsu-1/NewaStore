# Newa Store — Full E-Commerce Platform

A complete e-commerce web application built with **Django + PostgreSQL**. The
catalog is seeded with **real game data** (titles, prices, ratings, release
dates, cover art) fetched live from the [CheapShark](https://www.cheapshark.com/api/1.0)
and [SteamSpy](https://steamspy.com/) APIs — no API keys required. It ships
with a modern responsive storefront, guest + account checkout, multiple
payment gateways, order management, reviews, coupons, wishlists, SEO, a
customized admin dashboard, and a test suite.

## Data sources

| Source | What it provides | Used by |
|--------|------------------|---------|
| **CheapShark** (no key) | Active deals across 14 stores: real USD prices, Metacritic scores, Steam ratings, thumbnails | `populate_db.py` sweep + live search import |
| **Steam appdetails** (no key) | Genres, real descriptions, developers, publishers, release dates, HD screenshots | Seeder enrichment (featured games) |
| **SteamSpy** (no key) | The full Steam catalog (~60-80k games): names, prices in cents, developers | `manage.py import_steamspy` |

CheapShark's `page` parameter is broken, so the seeder enumerates the
catalog by **price band x sort x store** combos instead of paging. Images are
hotlinked from the Steam CDN (deterministic capsule URLs by appid), so no
bulk downloads are needed.

---

## Features

### Storefront
- **Home page** — Steam/Epic-style hero carousel with real game art, genre browse chips, featured & recommended, special deals, new releases, best sellers
- **Shop / catalog** — sidebar filters (genre, price, deals, search), sorting, pagination
- **Category & tag/genre pages**
- **Product detail** — 16:9 image gallery with HD screenshots, genre chips, Steam-style discount pricing, real release dates, tabs (description, specs, reviews, shipping), related games
- **Search** — live AJAX suggestions + zero-result live import (searches CheapShark and imports matches instantly)
- **JSON API** — `/api/search/`, `/api/browse/`, `/api/games/<slug>/`, `/api/genres/`, `/api/contact/`, `/api/health/` (Django REST Framework)
- **AJAX shop** — filter/sort without page reloads, infinite scroll pagination
- **Wishlist** — add/remove, move to cart
- **Reviews & ratings** — star ratings, verified-purchase badges, helpful votes
- **Responsive design** — dark/light theme toggle, mobile navigation
- **Static pages** — About, Contact, FAQ, Privacy, Terms, Shipping & Returns

### Cart & Checkout
- Persistent cart that works for **guests (session)** and **logged-in users**, merged on login
- Quantity updates, cart drawer, free-shipping progress bar
- **Coupons** — percentage, fixed, and free-shipping types with usage limits
- **Guest checkout** and account checkout
- Billing / shipping addresses (save for later)
- Configurable **shipping methods** with free-shipping thresholds
- Automatic **tax calculation**
- Payment methods: **eSewa, Khalti, Stripe, PayPal, Cash on Delivery, Bank Transfer**
- Order confirmation + status emails

### Orders
- Order history with status filter
- Order detail with status timeline
- Order tracking page
- **PDF invoice** download (ReportLab with HTML fallback)
- Cancel / reorder
- Admin bulk status actions and status history audit trail

### Accounts
- Registration, login (username **or** email), logout
- Password reset flow
- Dashboard with order/wishlist stats
- Profile editing, password change
- Address book (billing + shipping, default addresses)

### Admin (Jazzmin)
- Rich, dark-themed dashboard
- Full CRUD for products, categories, tags, orders, coupons, reviews, addresses, shipping, settings
- Inline product images & variants
- Bulk order status actions, review moderation, subscriber management

### Platform
- SEO: meta tags, Open Graph, `sitemap.xml`, `robots.txt`
- Custom 404 / 500 pages, maintenance mode
- Newsletter subscriptions, contact messages
- Signals to auto-create wishlists, track order status changes
- Byte-compiled-ready, environment-variable configuration
- 56 automated tests

---

## Quick Start

> Requires **Python 3.10+** and **PostgreSQL 14+** (service running; see `.env`).

```bash
# 0. Create the database (one-time, as the postgres superuser):
#    CREATE USER newastore WITH PASSWORD '...';
#    CREATE DATABASE newastore OWNER newastore;
#    ALTER ROLE newastore CREATEDB;   -- for Django's test runner
#    ...then put the credentials in .env (see .env example below)

# 1. Install dependencies
python -m pip install -r requirements.txt

# 2. Apply database migrations (PostgreSQL)
python manage.py migrate

# 3. Seed the catalog with real game data from the CheapShark sweep
python populate_db.py --reset

# 4. (Optional) import the FULL Steam catalog (~60-80k games, ~20 min)
python manage.py import_steamspy

# 5. Run the development server
python manage.py runserver
```

Then open <http://127.0.0.1:8000/>.

`.env` file (gitignored — copy the pattern):
```
DJANGO_DB=postgres          # or "sqlite" to fall back
DB_NAME=newastore
DB_USER=newastore
DB_PASSWORD=...
DB_HOST=localhost
DB_PORT=5432
```

### Demo credentials

| Role     | URL                    | Username | Password   |
|----------|------------------------|----------|------------|
| Admin    | `/admin/`              | `admin`  | `admin123` |
| Customer | `/login/`              | `demo`   | `demo1234` |

> Change these before deploying to production.

---

## Useful commands

```bash
python manage.py test store          # run the test suite
python manage.py createsuperuser     # create your own admin
python manage.py collectstatic       # gather static files (production)
python populate_db.py --reset        # re-seed the catalog from live APIs
python manage.py import_steamspy     # full Steam catalog import (resumable)
python manage.py import_steamspy --pages 5   # import 5 pages then stop
python smoke_test.py                 # hit every route and report status codes
```

---

## Project structure

```
newastore/
├── manage.py
├── populate_db.py            # seeds real game catalog from CheapShark + Steam APIs
├── smoke_test.py             # route smoke test
├── requirements.txt
├── db.sqlite3
├── media/                    # uploaded images (generated by seeder)
├── newastore/                # project config
│   ├── settings.py           # env-var driven, Jazzmin, payments
│   └── urls.py               # admin, sitemap, media, error handlers
└── store/
    ├── models.py             # 21 models (products, orders, cart, reviews, etc.)
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
| `Address` | Billing / shipping address book |
| `Order`, `OrderItem`, `OrderStatusHistory` | Orders with immutable line items & audit trail |
| `ShippingMethod` | Configurable shipping rates |
| `NewsletterSubscriber`, `ContactMessage` | Marketing & support |
| `SiteSettings` | Global store configuration (singleton) |

---

## Configuration

Key settings can be overridden via environment variables:

| Variable | Purpose |
|----------|---------|
| `DJANGO_SECRET_KEY` | Secret key (set in production) |
| `DJANGO_DEBUG` | `False` in production |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated host list |
| `DJANGO_DB` | `postgres` (default) or `sqlite` fallback |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection |
| `DJANGO_EMAIL_BACKEND` | Email backend (defaults to console) |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | SMTP |
| `ESEWA_MERCHANT_CODE`, `ESEWA_SECRET_KEY`, `ESEWA_URL` | eSewa |
| `KHALTI_PUBLIC_KEY`, `KHALTI_SECRET_KEY` | Khalti |
| `STRIPE_PUBLIC_KEY`, `STRIPE_SECRET_KEY` | Stripe publishes (pk_/sk_ test keys) |
| `STRIPE_WEBHOOK_SECRET`, `STRIPE_CURRENCY`, `STRIPE_PRICE_LABEL` | Stripe webhook signer + currency (Stripe has no NPR) |
| `PAYPAL_CLIENT_ID`, `PAYPAL_SECRET` | PayPal REST app credentials |
| `PAYPAL_WEBHOOK_ID`, `PAYPAL_CURRENCY`, `PAYPAL_PRICE_LABEL`, `PAYPAL_SANDBOX` | PayPal webhook verification + currency/mode |
| `SITE_BASE_URL` | Absolute base URL used in transactional emails |
| `USD_TO_NPR` | USD→NPR rate used by importers (default: 135) |
| `SEED_MAX_REQUESTS` | Max CheapShark requests in the sweep (default: 400) |
| `SEED_ENRICH` | Top games to Steam-enrich after the sweep (default: 150) |

By default email is printed to the console, so order confirmation and password
reset emails can be viewed in the terminal during development.

---

## Going to production (checklist)

1. Set `DJANGO_DEBUG=False` and a strong `DJANGO_SECRET_KEY`.
2. Configure `DJANGO_ALLOWED_HOSTS` (PostgreSQL is already the default database).
3. Configure SMTP email credentials.
4. Configure real payment gateway credentials (eSewa/Khalti live keys).
5. **Stripe webhooks**: `stripe listen --forward-to https://<host>/webhooks/stripe/`,
   then store the signing secret in `STRIPE_WEBHOOK_SECRET`.
6. **PayPal webhooks**: verify a `PAYMENT.CAPTURE.COMPLETED` webhook URL in the
   PayPal dashboard, paste the webhook ID into `PAYPAL_WEBHOOK_ID`, and set
   `PAYPAL_SANDBOX=False` for live mode.
7. Add WhiteNoise (already in `requirements.txt`) or a reverse proxy for static/media.
8. Run `python manage.py collectstatic`.
9. Serve behind HTTPS (security settings auto-enable when `DEBUG=False`).
