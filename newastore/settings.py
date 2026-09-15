import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-your-secret-key-here-change-in-production')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')

# Absolute base URL used in emails (order links, welcome CTA, …)
SITE_BASE_URL = os.environ.get('SITE_BASE_URL', 'http://localhost:8000')

# Jazzmin MUST be placed above django.contrib.admin.
# 'store' must sit above 'django.contrib.staticfiles': command discovery gives
# precedence to earlier apps, which lets our runserver override ship the
# self-healing bootstrap.
INSTALLED_APPS = [
    'jazzmin',
    'store',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',
    'django.contrib.humanize',
    'rest_framework',
]

REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 24,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '120/min',
        'contact': '5/min',
    },
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'newastore.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'store/templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'store.context_processors.site_settings',
                'store.context_processors.navigation',
                'store.context_processors.cart_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'newastore.wsgi.application'

# Database — PostgreSQL only (the SQLite fallback was removed).
# Always talks to the Postgres instance described in .env (psycopg3,
# non-default port 5433).
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'newastore'),
        'USER': os.environ.get('DB_USER', 'newastore'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5433'),
        'CONN_MAX_AGE': 60,  # persistent connections
    }
}

# Caching (genre nav, search-import negative cache, etc.)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'newastore-default',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kathmandu'
USE_I18N = True
USE_TZ = True

# Static & media
STATIC_URL = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'store/static')]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Authentication
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'home'
AUTHENTICATION_BACKENDS = [
    'store.backends.EmailOrUsernameModelBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# Messages -> framework-agnostic tags styled in templates
from django.contrib.messages import constants as messages
MESSAGE_TAGS = {
    messages.DEBUG: 'debug',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'error',
}

# Email
EMAIL_BACKEND = os.environ.get(
    'DJANGO_EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Newa Store <noreply@newastore.com>')

# Payments
# ---------------------------------------------------------------------------
# Secrets never belong in source control: everything below is read from .env,
# and the defaults are intentionally empty. eSewa/Khalti are SIMULATED in this
# demo store (see store/payments.py), so their credentials are not required to
# exercise the checkout flow.
# ---------------------------------------------------------------------------
ESEWA_MERCHANT_CODE = os.environ.get('ESEWA_MERCHANT_CODE', '')
ESEWA_SECRET_KEY = os.environ.get('ESEWA_SECRET_KEY', '')
ESEWA_URL = os.environ.get('ESEWA_URL', '')
KHALTI_PUBLIC_KEY = os.environ.get('KHALTI_PUBLIC_KEY', '')
KHALTI_SECRET_KEY = os.environ.get('KHALTI_SECRET_KEY', '')
STRIPE_PUBLIC_KEY = os.environ.get('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
# Webhook signing secret — REQUIRED in production (CLI: stripe listen --forward-to localhost:8000/webhooks/stripe/)
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
# Stripe does not support NPR; price the gateway charge in a supported currency.
STRIPE_CURRENCY = os.environ.get('STRIPE_CURRENCY', 'usd')
STRIPE_PRICE_LABEL = os.environ.get('STRIPE_PRICE_LABEL', 'USD $')
PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_CLIENT_ID', '')
PAYPAL_SECRET = os.environ.get('PAYPAL_SECRET', '')
PAYPAL_WEBHOOK_ID = os.environ.get('PAYPAL_WEBHOOK_ID', '')
PAYPAL_CURRENCY = os.environ.get('PAYPAL_CURRENCY', 'usd')
PAYPAL_PRICE_LABEL = os.environ.get('PAYPAL_PRICE_LABEL', 'USD $')
PAYPAL_SANDBOX = os.environ.get('PAYPAL_SANDBOX', 'True') == 'True'

# Security (enable in production)
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# Clean & User-Friendly Admin Dashboard Settings
JAZZMIN_SETTINGS = {
    "site_title": "Newa Store Admin",
    "site_header": "Newa Store",
    "site_brand": "Newa Store",
    "site_logo": "images/logo.png",
    "login_logo": "images/logo.png",
    "welcome_sign": "Welcome to Newa Store Dashboard",
    "copyright": "Newa Store",
    "search_model": ["auth.User", "store.Order", "store.Product"],
    "user_avatar": None,
    "topmenu_links": [
        {"name": "Dashboard", "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "Visit Live Store", "url": "/", "new_window": True},
    ],
    "usermenu_links": [
        {"name": "Visit Live Store", "url": "/", "new_window": True},
    ],
    "show_sidebar": True,
    "navigation_expanded": True,
    "hide_apps": [],
    "hide_models": [],
    "order_with_respect_to": ["store.Product", "store.Order", "store.Category", "auth"],
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "store.Product": "fas fa-box",
        "store.Category": "fas fa-tags",
        "store.Order": "fas fa-shopping-bag",
        "store.Coupon": "fas fa-ticket-alt",
        "store.Review": "fas fa-star",
        "store.Address": "fas fa-map-marker-alt",
        "store.Cart": "fas fa-shopping-cart",
        "store.Wishlist": "fas fa-heart",
        "store.NewsletterSubscriber": "fas fa-envelope",
        "store.ContactMessage": "fas fa-comments",
        "store.SiteSettings": "fas fa-cog",
        "store.ShippingMethod": "fas fa-truck",
    },
    "default_icon_parents": "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",
    "related_modal_active": True,
    "custom_css": "css/admin_custom.css",
    "show_ui_builder": False,
}

JAZZMIN_UI_TWEAKS = {
    "theme": "darkly",
    "default_theme_mode": "dark",
    "navbar": "navbar-dark",
    "sidebar": "sidebar-dark-primary",
    "brand_colour": "navbar-success",
    "accent": "accent-success",
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-success",
    },
}
