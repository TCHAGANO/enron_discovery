"""
Import du corpus Enron (maildir) dans PostgreSQL.

L'import travaille par lots : chaque lot est insere avec quelques requetes
groupees (bulk_create) au lieu de plusieurs dizaines de requetes par email.
Les employes et dossiers sont gardes en cache memoire. L'index plein texte et
les conversations sont calcules a la fin, en une passe.

L'import est idempotent : les Message-ID deja en base sont ignores.
"""

import email
import os
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from discovery.models import Attachment, Email, Employee, Folder
from discovery.parsing import parse_message
from discovery.search import update_search_vectors
from discovery.threads import build_threads

UNKNOWN_SENDER = "unknown@enron.local"


class Command(BaseCommand):
    help = "Import des emails du corpus Enron (incremental, par lots)"

    def add_arguments(self, parser):
        parser.add_argument("--path", required=True, help="Chemin vers le dossier maildir")
        parser.add_argument("--batch-size", type=int, default=1000, help="Emails par lot (defaut: 1000)")
        parser.add_argument("--limit", type=int, default=None, help="Arreter apres N fichiers (tests)")
        parser.add_argument("--skip-threads", action="store_true", help="Ne pas reconstruire les conversations")
        parser.add_argument("--skip-index", action="store_true", help="Ne pas calculer l'index plein texte")

    # ------------------------------------------------------------------ caches

    def _load_caches(self):
        self.employees = dict(Employee.objects.values_list("email", "id"))
        self.folders = dict(Folder.objects.values_list("path", "id"))
        self.existing_ids = set(Email.objects.values_list("message_id", flat=True))

    def _folder_id(self, root_path, directory):
        rel = os.path.relpath(directory, root_path)
        path = "" if rel == "." else rel.replace(os.sep, "/")
        path = path or "root"
        if path not in self.folders:
            parts = path.split("/")
            folder, _ = Folder.objects.get_or_create(
                path=path[:500],
                defaults={"name": parts[-1][:200], "mailbox": parts[0][:200] if len(parts) > 1 else ""},
            )
            self.folders[path] = folder.id
        return self.folders[path]

    def _ensure_employees(self, parsed_batch):
        names = {}
        for parsed in parsed_batch:
            people = [parsed.sender] if parsed.sender else []
            for addr, name in people + parsed.to + parsed.cc + parsed.bcc:
                if addr not in self.employees and (addr not in names or not names[addr]):
                    names[addr] = name
        if not names:
            return
        Employee.objects.bulk_create(
            [Employee(email=addr, name=(name or addr.split("@")[0])[:200]) for addr, name in names.items()],
            ignore_conflicts=True,
            batch_size=1000,
        )
        self.employees.update(Employee.objects.filter(email__in=list(names)).values_list("email", "id"))

    # ------------------------------------------------------------------ lots

    def _save_batch(self, batch):
        """batch: liste de (ParsedEmail, folder_id)."""
        parsed_list = [p for p, _ in batch]
        with transaction.atomic():
            self._ensure_employees(parsed_list)
            emails = Email.objects.bulk_create(
                [
                    Email(
                        message_id=p.message_id,
                        subject=p.subject,
                        normalized_subject=p.normalized_subject,
                        body=p.body,
                        date=p.date,
                        from_employee_id=self.employees[p.sender[0]] if p.sender else self.unknown_sender_id,
                        folder_id=folder_id,
                        in_reply_to_header=p.in_reply_to,
                    )
                    for p, folder_id in batch
                ],
                batch_size=500,
            )

            links = {"to": [], "cc": [], "bcc": []}
            attachments = []
            for obj, p in zip(emails, parsed_list):
                for kind in links:
                    for addr, _name in getattr(p, kind):
                        links[kind].append((obj.id, self.employees[addr]))
                for filename, content_type, size in p.attachments:
                    attachments.append(
                        Attachment(email_id=obj.id, filename=filename, content_type=content_type, size=size)
                    )

            for kind, field in (("to", Email.to_employees), ("cc", Email.cc_employees), ("bcc", Email.bcc_employees)):
                through = field.through
                through.objects.bulk_create(
                    [through(email_id=e, employee_id=emp) for e, emp in links[kind]],
                    ignore_conflicts=True,
                    batch_size=2000,
                )
            Attachment.objects.bulk_create(attachments, batch_size=1000)
        return len(emails)

    # ------------------------------------------------------------------ main

    def handle(self, *args, **options):
        root_path = os.path.abspath(options["path"])
        if not os.path.isdir(root_path):
            raise CommandError(f"Chemin invalide: {root_path}")
        batch_size = max(1, options["batch_size"])
        limit = options["limit"]

        started = time.monotonic()
        self.stdout.write(f"Debut import: {root_path}")
        self._load_caches()
        self.stdout.write(f"Message-ID deja en base: {len(self.existing_ids)}")

        unknown, _ = Employee.objects.get_or_create(email=UNKNOWN_SENDER, defaults={"name": "Unknown Sender"})
        self.unknown_sender_id = unknown.id
        self.employees[UNKNOWN_SENDER] = unknown.id

        stats = {"processed": 0, "imported": 0, "already": 0, "errors": 0, "invalid_dates": 0}
        batch = []

        def flush():
            if batch:
                stats["imported"] += self._save_batch(batch)
                batch.clear()

        done = False
        for directory, dirs, files in os.walk(root_path):
            dirs.sort()
            folder_id = None
            for filename in sorted(files):
                if limit is not None and stats["processed"] >= limit:
                    done = True
                    break
                stats["processed"] += 1
                filepath = os.path.join(directory, filename)
                try:
                    with open(filepath, "rb") as fh:
                        msg = email.message_from_binary_file(fh)
                    # Repli sur le chemin relatif : les noms de fichier ("1.", "2."...)
                    # se repetent d'un dossier a l'autre.
                    fallback_id = "<file:" + os.path.relpath(filepath, root_path).replace(os.sep, "/") + ">"
                    message_id = (msg.get("Message-ID") or "").strip() or fallback_id
                    if message_id in self.existing_ids:
                        stats["already"] += 1
                        continue
                    parsed = parse_message(msg, fallback_id)
                    if parsed.date_was_invalid:
                        stats["invalid_dates"] += 1
                    if folder_id is None:
                        folder_id = self._folder_id(root_path, directory)
                    self.existing_ids.add(parsed.message_id)
                    batch.append((parsed, folder_id))
                except Exception as exc:  # un fichier illisible ne doit pas stopper l'import
                    stats["errors"] += 1
                    self.stderr.write(f"Erreur: {filepath} -> {exc}")

                if len(batch) >= batch_size:
                    flush()
                if stats["processed"] % 10000 == 0:
                    self._progress(stats, started)
            if done:
                break
        flush()
        self._progress(stats, started)

        if not options["skip_index"]:
            self.stdout.write("Calcul de l'index plein texte...")
            indexed = update_search_vectors(log=self.stdout.write)
            self.stdout.write(f"Emails indexes: {indexed}")

        if not options["skip_threads"]:
            self.stdout.write("Reconstruction des conversations...")
            tstats = build_threads(log=self.stdout.write)
            self.stdout.write(
                f"Conversations: {tstats['threads']} | liens In-Reply-To: {tstats['header_links']} | "
                f"reponses deduites: {tstats['inferred_replies']}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import termine en {time.monotonic() - started:.0f}s : traites={stats['processed']}, "
                f"importes={stats['imported']}, deja={stats['already']}, erreurs={stats['errors']}, "
                f"dates_invalides={stats['invalid_dates']}"
            )
        )

    def _progress(self, stats, started):
        elapsed = max(time.monotonic() - started, 1e-6)
        self.stdout.write(
            f"Progression: {stats['processed']} fichiers | importes={stats['imported']} "
            f"deja={stats['already']} erreurs={stats['errors']} | {stats['processed'] / elapsed:.0f} fichiers/s"
        )
