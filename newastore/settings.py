import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name, default=''):
    return [item.strip() for item in os.environ.get(name, default).split(',') if item.strip()]


DEBUG = env_bool('DJANGO_DEBUG', False)

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        # An ephemeral key is safer than a shared literal. Set DJANGO_SECRET_KEY
        # in .env when stable development sessions are desired.
        SECRET_KEY = secrets.token_urlsafe(64)
    else:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY must be set in the environment when DEBUG is False.'
        )

if not DEBUG and (
    len(SECRET_KEY) < 50
    or SECRET_KEY.startswith('django-insecure-')
    or SECRET_KEY in {'dev-insecure-key-change-in-production', 'CHANGE-ME'}
):
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY must be a unique, high-entropy value of at least 50 characters.'
    )

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')
if not DEBUG and (not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        'DJANGO_ALLOWED_HOSTS must explicitly list production hostnames; wildcards are forbidden.'
    )

SITE_BASE_URL = os.environ.get('SITE_BASE_URL', 'http://localhost:8000')
if not DEBUG:
    _site_url = urlsplit(SITE_BASE_URL)
    if (
        _site_url.scheme != 'https' or not _site_url.hostname
        or _site_url.username or _site_url.password
        or _site_url.path not in ('', '/') or _site_url.query or _site_url.fragment
    ):
        raise ImproperlyConfigured('SITE_BASE_URL must be an absolute HTTPS URL in production.')
    _site_host = _site_url.hostname.lower()
    if not any(
        _site_host == host.lower() or (
            host.startswith('.') and _site_host.endswith(host.lower())
        )
        for host in ALLOWED_HOSTS
    ):
        raise ImproperlyConfigured('SITE_BASE_URL hostname must be present in DJANGO_ALLOWED_HOSTS.')

ADMIN_URL = os.environ.get('DJANGO_ADMIN_PATH', 'admin').strip().strip('/') or 'admin'
if any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in ADMIN_URL):
    raise ImproperlyConfigured('DJANGO_ADMIN_PATH may contain only letters, numbers, hyphens and underscores.')
ADMIN_ALLOWED_NETWORKS = env_list('DJANGO_ADMIN_ALLOWED_NETWORKS')
TRUST_X_FORWARDED_FOR = env_bool('DJANGO_TRUST_X_FORWARDED_FOR', False)

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
        'store.throttles.SecureAnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '120/min',
        'contact': '5/min',
    },
    'DEFAULT_RENDERER_CLASSES': (
        ['rest_framework.renderers.JSONRenderer']
        if not DEBUG else
        ['rest_framework.renderers.JSONRenderer', 'rest_framework.renderers.BrowsableAPIRenderer']
    ),
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'store.middleware.SecurityHeadersMiddleware',
    'store.middleware.AdminAccessMiddleware',
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
                'store.context_processors.security_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'newastore.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'newastore'),
        'USER': os.environ.get('DB_USER', 'newastore'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5433'),
        'CONN_MAX_AGE': 60,
        'CONN_HEALTH_CHECKS': True,
    }
}

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    import dj_database_url
    DATABASES['default'] = dj_database_url.parse(
        DATABASE_URL, conn_max_age=60, conn_health_checks=True,
        ssl_require=not DEBUG,
    )

if 'postgresql' not in DATABASES['default']['ENGINE']:
    raise ImproperlyConfigured('NewaStore requires PostgreSQL; other database engines are unsupported.')

if not DEBUG and not DATABASE_URL and not DATABASES['default']['PASSWORD']:
    raise ImproperlyConfigured('A database password or DATABASE_URL is required in production.')
if not DEBUG and not DATABASE_URL:
    _db_sslmode = os.environ.get('DB_SSLMODE', 'require').strip().lower()
    if _db_sslmode not in {'require', 'verify-ca', 'verify-full'}:
        raise ImproperlyConfigured(
            'DB_SSLMODE must be require, verify-ca, or verify-full in production.'
        )
    DATABASES['default'].setdefault('OPTIONS', {})['sslmode'] = _db_sslmode

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'newastore-default',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kathmandu'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = []
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}
WHITENOISE_MAX_AGE = 31536000 if not DEBUG else 0
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

ARTWORK_THUMB_WIDTH = int(os.environ.get('ARTWORK_THUMB_WIDTH', 616))
ARTWORK_WEBP_QUALITY = int(os.environ.get('ARTWORK_WEBP_QUALITY', 82))

SERVE_REMOTE_ARTWORK = os.environ.get('SERVE_REMOTE_ARTWORK', 'False') == 'True'
ARTWORK_ALLOWED_HOSTS = env_list('ARTWORK_ALLOWED_HOSTS')
ENABLE_LIVE_SEARCH_IMPORT = env_bool('ENABLE_LIVE_SEARCH_IMPORT', DEBUG)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
LOGOUT_REDIRECT_URL = 'home'
AUTHENTICATION_BACKENDS = [
    'store.backends.EmailOrUsernameModelBackend',
]
AUTH_PAIR_FAILURE_LIMIT = int(os.environ.get('AUTH_PAIR_FAILURE_LIMIT', '5'))
AUTH_IP_FAILURE_LIMIT = int(os.environ.get('AUTH_IP_FAILURE_LIMIT', '25'))
AUTH_FAILURE_WINDOW = int(os.environ.get('AUTH_FAILURE_WINDOW', '900'))
AUTH_BLOCK_DURATION = int(os.environ.get('AUTH_BLOCK_DURATION', '900'))
AUTH_REQUEST_LIMIT = int(os.environ.get('AUTH_REQUEST_LIMIT', '20'))
AUTH_REQUEST_WINDOW = int(os.environ.get('AUTH_REQUEST_WINDOW', '60'))
PASSWORD_RESET_TIMEOUT = int(os.environ.get('PASSWORD_RESET_TIMEOUT', '3600'))
if min(
    AUTH_PAIR_FAILURE_LIMIT, AUTH_IP_FAILURE_LIMIT, AUTH_FAILURE_WINDOW,
    AUTH_BLOCK_DURATION, AUTH_REQUEST_LIMIT, AUTH_REQUEST_WINDOW,
    PASSWORD_RESET_TIMEOUT,
) <= 0:
    raise ImproperlyConfigured('Authentication limits and timeouts must be positive integers.')

from django.contrib.messages import constants as messages
MESSAGE_TAGS = {
    messages.DEBUG: 'debug',
    messages.INFO: 'info',
    messages.SUCCESS: 'success',
    messages.WARNING: 'warning',
    messages.ERROR: 'error',
}

EMAIL_BACKEND = os.environ.get(
    'DJANGO_EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend',
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', False)
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'NewaStore <newastore8@gmail.com>')
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT', '10'))
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    raise ImproperlyConfigured('EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be enabled.')
if not DEBUG and EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend':
    if not all((EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD)):
        raise ImproperlyConfigured('Production SMTP requires host, username and password.')
    if not (EMAIL_USE_TLS or EMAIL_USE_SSL):
        raise ImproperlyConfigured('Production SMTP must use TLS or SSL.')

ESEWA_PRODUCT_CODE = os.environ.get('ESEWA_PRODUCT_CODE', 'EPAYTEST' if DEBUG else '')
ESEWA_SECRET_KEY = os.environ.get('ESEWA_SECRET_KEY', '')
ESEWA_FORM_URL = os.environ.get(
    'ESEWA_FORM_URL',
    'https://rc-epay.esewa.com.np/api/epay/main/v2/form' if DEBUG else '',
)
ESEWA_STATUS_URL = os.environ.get(
    'ESEWA_STATUS_URL',
    'https://rc.esewa.com.np/api/epay/transaction/status/' if DEBUG else '',
)

# Default to the REAL eSewa gateway: with the EPAYTEST sandbox credentials above
# the signed form posts to eSewa's own hosted RC test page (no real money moves),
# which is what shoppers actually see. Set ESEWA_SIMULATE=true to fall back to the
# in-site demo page, or ESEWA_SIMULATE=auto for the legacy "simulate on EPAYTEST" rule.
_esewa_simulate = os.environ.get('ESEWA_SIMULATE', 'false').strip().lower()
if _esewa_simulate == 'auto':
    ESEWA_SIMULATE = ESEWA_PRODUCT_CODE == 'EPAYTEST'
else:
    ESEWA_SIMULATE = _esewa_simulate in ('1', 'true', 'yes', 'on')
ESEWA_VERIFY_STATUS = env_bool('ESEWA_VERIFY_STATUS', not DEBUG)

BANK_TRANSFER_ENABLED = env_bool('BANK_TRANSFER_ENABLED', DEBUG)
BANK_TRANSFER_BANK_NAME = os.environ.get(
    'BANK_TRANSFER_BANK_NAME', 'Nay Bank' if DEBUG else ''
)
BANK_TRANSFER_ACCOUNT_NAME = os.environ.get(
    'BANK_TRANSFER_ACCOUNT_NAME', 'NewaStore Pvt. Ltd.' if DEBUG else ''
)
BANK_TRANSFER_ACCOUNT_NUMBER = os.environ.get(
    'BANK_TRANSFER_ACCOUNT_NUMBER', '0123456789012' if DEBUG else ''
)
BANK_TRANSFER_BRANCH = os.environ.get(
    'BANK_TRANSFER_BRANCH', 'Kathmandu — New Road' if DEBUG else ''
)
BANK_TRANSFER_SWIFT = os.environ.get(
    'BANK_TRANSFER_SWIFT', 'NAYBNPKA' if DEBUG else ''
)
ESEWA_ORDER_TTL_MINUTES = int(os.environ.get('ESEWA_ORDER_TTL_MINUTES', '30'))
BANK_TRANSFER_ORDER_TTL_HOURS = int(os.environ.get('BANK_TRANSFER_ORDER_TTL_HOURS', '168'))
if ESEWA_ORDER_TTL_MINUTES < 5 or BANK_TRANSFER_ORDER_TTL_HOURS < 1:
    raise ImproperlyConfigured('Payment reservation TTLs are below the safe minimum.')
if not DEBUG and BANK_TRANSFER_ENABLED and not all((
    BANK_TRANSFER_BANK_NAME, BANK_TRANSFER_ACCOUNT_NAME,
    BANK_TRANSFER_ACCOUNT_NUMBER, BANK_TRANSFER_BRANCH,
)):
    raise ImproperlyConfigured(
        'Enabled bank transfer requires bank name, account name/number and branch.'
    )
if not DEBUG and BANK_TRANSFER_ENABLED and BANK_TRANSFER_ACCOUNT_NUMBER.strip().upper() in {
    'CHANGE-ME', '0123456789012',
}:
    raise ImproperlyConfigured('Replace the placeholder bank account number before enabling transfers.')

if ESEWA_SIMULATE and not DEBUG:
    raise ImproperlyConfigured('ESEWA_SIMULATE is development-only and cannot be enabled in production.')
for _name, _url in (('ESEWA_FORM_URL', ESEWA_FORM_URL), ('ESEWA_STATUS_URL', ESEWA_STATUS_URL)):
    if _url:
        _parsed = urlsplit(_url)
        _host = (_parsed.hostname or '').lower().rstrip('.')
        if (
            _parsed.scheme != 'https' or not _host
            or _parsed.username or _parsed.password
            or not (_host == 'esewa.com.np' or _host.endswith('.esewa.com.np'))
        ):
            raise ImproperlyConfigured(f'{_name} must be an absolute HTTPS URL without embedded credentials.')

X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', '28800' if not DEBUG else '1209600'))
SESSION_EXPIRE_AT_BROWSER_CLOSE = not DEBUG
SESSION_SAVE_EVERY_REQUEST = not DEBUG
CSRF_TRUSTED_ORIGINS = env_list('DJANGO_CSRF_TRUSTED_ORIGINS')
if not DEBUG and any(not origin.startswith('https://') for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured('Every DJANGO_CSRF_TRUSTED_ORIGINS entry must use HTTPS.')

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    if env_bool('DJANGO_TRUST_X_FORWARDED_PROTO', False):
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_NAME = '__Host-sessionid'
    CSRF_COOKIE_NAME = '__Host-csrftoken'
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'security': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'security'},
    },
    'loggers': {
        'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'store.security': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}

JAZZMIN_SETTINGS = {
    "site_title": "NewaStore Admin",
    "site_header": "NewaStore",
    "site_brand": "NewaStore",
    "site_logo": "images/logo.png",
    "login_logo": "images/logo.png",
    "welcome_sign": "Welcome to NewaStore Dashboard",
    "copyright": "NewaStore",
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
        "store.Cart": "fas fa-shopping-cart",
        "store.Wishlist": "fas fa-heart",
        "store.NewsletterSubscriber": "fas fa-envelope",
        "store.ContactMessage": "fas fa-comments",
        "store.SiteSettings": "fas fa-cog",
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
