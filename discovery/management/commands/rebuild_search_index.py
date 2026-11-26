from django.core.management.base import BaseCommand

from discovery.models import Email
from discovery.search import search_config, update_search_vectors


class Command(BaseCommand):
    help = "Recalcule l'index plein texte de tous les emails (apres un changement de SEARCH_CONFIG)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Ne traite que les emails sans index",
        )

    def handle(self, *args, **options):
        qs = Email.objects.filter(search_vector__isnull=True) if options["missing_only"] else Email.objects.all()
        count = update_search_vectors(qs)
        self.stdout.write(self.style.SUCCESS(f"{count} emails indexes (config '{search_config()}')"))
