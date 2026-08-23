"""
Django settings for svc-locations-django microservice.
Admin is disabled — no django_admin_log table.
"""

import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

# Legacy Pedro imports still use MySQLdb even when Django runs on Postgres.
pymysql.install_as_MySQLdb()

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

SECRET_KEY = 'django-insecure-change-me-svc-locations-django'

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'corsheaders',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'locations',
    'product',
    'recipe',
    'stock_ledger',
    'planning',
    'purchasing',
    'users_rbac',
    'hardware',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.OpsErrorMiddleware',
    'core.middleware.ApiAuditMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

_DB_ENGINE = os.getenv('DB_ENGINE', 'mysql').lower()
if _DB_ENGINE in ('postgres', 'postgresql'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('PG_DB_NAME') or os.getenv('DB_NAME'),
            'USER': os.getenv('PG_DB_USER') or os.getenv('DB_USER'),
            'PASSWORD': os.getenv('PG_DB_PASSWORD') or os.getenv('DB_PASSWORD'),
            'HOST': os.getenv('PG_DB_HOST') or os.getenv('DB_HOST'),
            'PORT': os.getenv('PG_DB_PORT') or '5432',
            'OPTIONS': {'sslmode': 'require'},
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': os.getenv('DB_NAME'),
            'USER': os.getenv('DB_USER'),
            'PASSWORD': os.getenv('DB_PASSWORD'),
            'HOST': os.getenv('DB_HOST'),
            'PORT': os.getenv('DB_PORT'),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = [
    'http://localhost:5173',
    'http://localhost:5174',
    'https://beta.gazeboo.cloud',
    'https://gazeboo.cloud',
    'https://www.gazeboo.cloud',
    'http://dev.gazeboo.cloud',
    'https://dev.gazeboo.cloud',
]

CSRF_TRUSTED_ORIGINS = [
    'http://localhost:5173',
    'http://localhost:5174',
    'https://beta.gazeboo.cloud',
    'https://gazeboo.cloud',
    'https://www.gazeboo.cloud',
    'http://dev.gazeboo.cloud',
    'https://dev.gazeboo.cloud',
]

AUDIT_S3_BUCKET = os.getenv('AUDIT_S3_BUCKET', 'gazebo-audit-logging')
MEDIA_S3_BUCKET = os.getenv('MEDIA_S3_BUCKET', 'gazebo-media-files')
APP_MIN_VERSION_ANDROID = os.getenv('APP_MIN_VERSION_ANDROID', '1.0.1')
APP_LATEST_VERSION_ANDROID = os.getenv('APP_LATEST_VERSION_ANDROID', '') or APP_MIN_VERSION_ANDROID
APP_UPDATE_MESSAGE = os.getenv(
    'APP_UPDATE_MESSAGE',
    'Hand this device to IT to install the update.',
)
APP_VERSION_API_TOKEN = os.getenv('APP_VERSION_API_TOKEN', '')
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
AWS_PROFILE = os.getenv('AWS_PROFILE')
AWS_DEFAULT_REGION = os.getenv('AWS_DEFAULT_REGION', 'eu-west-2')
CORS_ALLOW_HEADERS = (
    'accept',
    'authorization',
    'content-type',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'x-api-token',
    'x-device-serial',
    'x-device-nickname',
    'x-device-ip',
)
