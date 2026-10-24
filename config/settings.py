"""
config/settings.py  (SolvexMarketInc)
All configuration comes from environment variables via python-decouple.
"""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from corsheaders.defaults import default_headers
import os


import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- core
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:5173", cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
CORS_EXPOSE_HEADERS = ["Retry-After"]
CORS_ALLOW_HEADERS = list(default_headers) + ["idempotency-key"]

AUTH_USER_MODEL = "authentication.User"  # MUST be set before the first migrate

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",

    # project apps (uncomment each one as you build it)
    "core",
    "authentication",
    "profiles",
    "kyc",
    "wallet",
    "referral",
    "trade",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,  # picks up <app>/templates/<app>/*.html
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------- database
DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL'),
        conn_max_age=0,
        ssl_require=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------- i18n / time
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------- static
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- DRF / JWT / OpenAPI
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPagination",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "120/min",
        "auth_sensitive": "10/min",  # register, login, otp, reset
        "otp_send": "5/hour",
        "withdrawal": "10/hour",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "SolvexMarketInc API",
    "DESCRIPTION": "Backend API for the SolvexMarketInc crypto + forex trading platform. "
                   "All money is USD unless stated.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SECURITY": [{"jwtAuth": []}],
    "TAGS": [{"name": n} for n in
             ["System", "Authentication", "Profile", "KYC", "Wallet", "Referral", "Trade", "Cron"]],
}

# --------------------------------------------------------------------------- email (Mailgun)
MAILGUN_API_KEY = config("MAILGUN_API_KEY", default="")      # empty is OK locally when DEBUG=True
MAILGUN_DOMAIN = config("MAILGUN_DOMAIN", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="SolvexMarketInc <no-reply@localhost>")
NOTICE_FROM_EMAIL = config("NOTICE_FROM_EMAIL", default="SolvexMarketInc <notices@localhost>")
ADMIN_NOTIFICATION_EMAIL = config("ADMIN_NOTIFICATION_EMAIL", default="")

# --------------------------------------------------------------------------- cron / frontend
CRON_SECRET_KEY = config("CRON_SECRET_KEY", default="")      # empty => cron endpoints refuse everything
FRONTEND_URL = config("FRONTEND_URL", default="http://localhost:5173").split(",")[0].strip().rstrip("/")
FRONTEND_SIGNUP_PATH = config("FRONTEND_SIGNUP_PATH", default="/signup")
FRONTEND_RESET_PASSWORD_PATH = config("FRONTEND_RESET_PASSWORD_PATH", default="/reset-password")

# --------------------------------------------------------------------------- auth rules
OTP_LENGTH = config("OTP_LENGTH", default=6, cast=int)
OTP_EXPIRY_MINUTES = config("OTP_EXPIRY_MINUTES", default=10, cast=int)
OTP_MAX_ATTEMPTS = config("OTP_MAX_ATTEMPTS", default=5, cast=int)
OTP_RESEND_COOLDOWN_SECONDS = config("OTP_RESEND_COOLDOWN_SECONDS", default=60, cast=int)
PASSWORD_RESET_TOKEN_EXPIRY_MINUTES = config("PASSWORD_RESET_TOKEN_EXPIRY_MINUTES", default=30, cast=int)
LOGIN_MAX_FAILED_ATTEMPTS = config("LOGIN_MAX_FAILED_ATTEMPTS", default=5, cast=int)
LOGIN_LOCKOUT_MINUTES = config("LOGIN_LOCKOUT_MINUTES", default=15, cast=int)

# --------------------------------------------------------------------------- wallet / referral / trade (used by later apps)
MIN_DEPOSIT_AMOUNT = config("MIN_DEPOSIT_AMOUNT", default="10.00", cast=Decimal)
MIN_WITHDRAWAL_AMOUNT = config("MIN_WITHDRAWAL_AMOUNT", default="20.00", cast=Decimal)
PENDING_TRANSACTION_EXPIRY_MINUTES = config("PENDING_TRANSACTION_EXPIRY_MINUTES", default=5, cast=int)
USDT_USD_RATE = config("USDT_USD_RATE", default="1.00", cast=Decimal)
DEPOSIT_ADDRESS_BEP20 = config("DEPOSIT_ADDRESS_BEP20", default="")
DEPOSIT_ADDRESS_ERC20 = config("DEPOSIT_ADDRESS_ERC20", default="")
DEPOSIT_ADDRESS_TRC20 = config("DEPOSIT_ADDRESS_TRC20", default="")
REFERRAL_BONUS_USD = config("REFERRAL_BONUS_USD", default="3.00", cast=Decimal)
TRADE_MIN_AMOUNT = config("TRADE_MIN_AMOUNT", default="10.00", cast=Decimal)
TRADE_MAX_LEVERAGE = config("TRADE_MAX_LEVERAGE", default=100, cast=int)
TRADE_SIM_MIN_MOVE_PCT = config("TRADE_SIM_MIN_MOVE_PCT", default="-2.0", cast=Decimal)
TRADE_SIM_MAX_MOVE_PCT = config("TRADE_SIM_MAX_MOVE_PCT", default="3.0", cast=Decimal)
TRADING_TIMEZONE = config("TRADING_TIMEZONE", default="UTC")
PROFILE_LOCK_IDENTITY_AFTER_KYC = config("PROFILE_LOCK_IDENTITY_AFTER_KYC", default=True, cast=bool)

WALLET_MAX_PENDING_DEPOSITS = config("WALLET_MAX_PENDING_DEPOSITS", default=5, cast=int)
WALLET_MAX_OTP_RESENDS = config("WALLET_MAX_OTP_RESENDS", default=3, cast=int)

KYC_MIN_AGE = int(os.environ.get("KYC_MIN_AGE", 18))
KYC_MAX_ATTEMPTS = int(os.environ.get("KYC_MAX_ATTEMPTS", 5))
KYC_POLICY_VERSION = os.environ.get("KYC_POLICY_VERSION", "1.0")


# --------------------------------------------------------------------------- production hardening
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Render sits behind a proxy

# --------------------------------------------------------------------------- logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "[{levelname}] {asctime} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}


# ─── JAZZMIN ──────────────────────────────────────────────
JAZZMIN_SETTINGS = {
    "site_title": "SolvexMarketInc Admin",
    "site_header": "SolvexMarketInc",
    "site_brand": "SolvexMarketInc",
    "welcome_sign": "Welcome to the SolvexMarketInc Control Panel",
    "copyright": "SolvexMarketInc",
    "search_model": ["authentication.User"],
    "topmenu_links": [
        {"name": "Home",  "url": "admin:index", "permissions": ["auth.view_user"]},
        {"name": "Site",  "url": "/", "new_window": True},
        {"name": "Docs",  "url": "/swagger/", "new_window": True},
    ],
    "show_sidebar":         True,
    "navigation_expanded":  True,
    "icons": {
        "accounts":             "fas fa-users-cog",
        "accounts.User":        "fas fa-user",
        "accounts.OTPCode":     "fas fa-key",
        "wallet":               "fas fa-wallet",
        "investment":           "fas fa-chart-line",
        "management":           "fas fa-cogs",
        "auth.Group":           "fas fa-layer-group",
    },
    "default_icon_parents":  "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",
    "related_modal_active":   True,
    "use_google_fonts_cdn":   True,
    "show_ui_builder":        False,
    "changeform_format":      "horizontal_tabs",
    "language_chooser":       False,
}

JAZZMIN_UI_TWEAKS = {
    "navbar_small_text":   False,
    "footer_small_text":   False,
    "body_small_text":     False,
    "brand_small_text":    False,
    "brand_colour":        "navbar-primary",
    "accent":              "accent-primary",
    "navbar":              "navbar-dark",
    "no_navbar_border":    False,
    "navbar_fixed":        True,
    "layout_boxed":        False,
    "footer_fixed":        False,
    "sidebar_fixed":       True,
    "sidebar":             "sidebar-dark-primary",
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand":  False,
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style":  False,
    "sidebar_nav_flat_style":    False,
    "theme":               "default",
    "dark_mode_theme":     "auto",
    "button_classes": {
        "primary":  "btn-primary",
        "secondary":"btn-secondary",
        "info":     "btn-info",
        "warning":  "btn-warning",
        "danger":   "btn-danger",
        "success":  "btn-success",
    },
}