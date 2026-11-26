from django.apps import AppConfig


class DiscoveryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "discovery"
    # Le label reste "polls" pour garder les tables (polls_email, ...) et
    # l'historique des migrations des bases deja importees.
    label = "polls"
    verbose_name = "Enron Discovery"
