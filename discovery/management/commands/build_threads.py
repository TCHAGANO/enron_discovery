from django.core.management.base import BaseCommand

from discovery.threads import build_threads


class Command(BaseCommand):
    help = "Reconstruit les conversations (In-Reply-To + sujet normalise et participants)"

    def handle(self, *args, **options):
        stats = build_threads(log=self.stdout.write)
        self.stdout.write(
            self.style.SUCCESS(
                f"Threads: {stats['threads']} conversations, "
                f"{stats['header_links']} liens In-Reply-To, "
                f"{stats['inferred_replies']} reponses deduites"
            )
        )
