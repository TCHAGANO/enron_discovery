"""
Configuration Django du projet Enron Discovery.

Les valeurs sensibles ou propres a chaque machine sont lues dans les variables
d'environnement (ou dans un fichier .env a la racine, voir .env.example).
Les valeurs par defaut correspondent a l'environnement de developpement
(docker-compose.yml) et a la CI.
"""

import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", default=True)

# En production, DJANGO_SECRET_KEY est obligatoire. En developpement, une cle
# aleatoire est generee a chaque demarrage si aucune n'est fournie.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or (get_random_secret_key() if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY doit etre defini quand DJANGO_DEBUG=False")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "discovery.apps.DiscoveryConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "enron_discovery.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "enron_discovery.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "enron_db"),
        "USER": os.getenv("POSTGRES_USER", "enron"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "enron2026"),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "5433"),
    }
}

# Cache utilise pour les statistiques couteuses (dashboard, graphe).
# Par defaut en memoire ; definir DJANGO_CACHE_LOCATION pour un cache fichier
# partage entre processus (ex: /tmp/enron_cache).
if os.getenv("DJANGO_CACHE_LOCATION"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": os.getenv("DJANGO_CACHE_LOCATION"),
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Duree de cache (secondes) des statistiques du dashboard et du graphe.
STATS_CACHE_SECONDS = int(os.getenv("STATS_CACHE_SECONDS", "600"))

# Configuration linguistique PostgreSQL utilisee pour la recherche plein texte.
# Doit etre identique a l'indexation et a la recherche.
SEARCH_CONFIG = os.getenv("SEARCH_CONFIG", "english")


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
