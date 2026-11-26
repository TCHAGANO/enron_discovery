"""
Reconstruction des conversations (threads).

Le corpus Enron ne contient presque jamais d'en-tete In-Reply-To. On combine
donc deux sources :

1. les en-tetes In-Reply-To, resolus en SQL une fois tous les emails importes
   (l'ordre d'import n'a donc plus d'importance) ;
2. une heuristique : deux emails appartiennent a la meme conversation s'ils
   ont le meme sujet normalise (sans RE:/FW:), partagent au moins un
   participant et sont separes de moins de THREAD_WINDOW.

Pour chaque email rattache sans In-Reply-To, le parent deduit est le message
precedent du thread dont l'expediteur courant etait destinataire (une vraie
reponse), a defaut le dernier message du thread. Les copies d'un meme email
(meme expediteur, meme date) presentes dans plusieurs boites mail ne sont pas
traitees comme des reponses.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.db import connection, transaction

from .models import Email, Thread

THREAD_WINDOW = timedelta(days=60)
BATCH_SIZE = 5000


@dataclass
class _Msg:
    id: int
    date: object
    sender: int
    recipients: set
    parent: int | None
    inferred: bool = False


@dataclass
class _Cluster:
    messages: list = field(default_factory=list)
    participants: set = field(default_factory=set)
    last_date: object = None
    ids: set = field(default_factory=set)

    def add(self, msg):
        self.messages.append(msg)
        self.ids.add(msg.id)
        self.participants.add(msg.sender)
        self.participants.update(msg.recipients)
        if msg.date and (self.last_date is None or msg.date > self.last_date):
            self.last_date = msg.date


def resolve_reply_headers():
    """Relie in_reply_to a partir de l'en-tete brut. Renvoie le nombre de liens."""
    table = Email._meta.db_table
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE {table} AS e
            SET in_reply_to_id = p.id
            FROM {table} AS p
            WHERE e.in_reply_to_header <> ''
              AND p.message_id = e.in_reply_to_header
              AND p.id <> e.id
              AND e.in_reply_to_id IS DISTINCT FROM p.id
            """
        )
        return cursor.rowcount


def _recipients_for(ids):
    """{email_id: set(employee_id)} pour To + Cc."""
    result = {i: set() for i in ids}
    for through in (Email.to_employees.through, Email.cc_employees.through):
        rows = through.objects.filter(email_id__in=ids).values_list("email_id", "employee_id")
        for email_id, employee_id in rows:
            result[email_id].add(employee_id)
    return result


def _is_within_window(cluster, msg):
    if msg.date is None or cluster.last_date is None:
        return True
    return abs(msg.date - cluster.last_date) <= THREAD_WINDOW


def cluster_group(messages):
    """
    Regroupe les messages d'un meme sujet (tries par date) en conversations
    et deduit un parent pour chaque reponse. Fonction pure, testable.

    Renvoie une liste de clusters ; chaque _Msg.parent est mis a jour.
    """
    clusters = []
    by_id = {}
    for msg in messages:
        participants = {msg.sender} | msg.recipients
        target = None
        if msg.parent is not None and msg.parent in by_id:
            target = by_id[msg.parent]
        else:
            for cluster in reversed(clusters):
                if cluster.participants & participants and _is_within_window(cluster, msg):
                    target = cluster
                    break

        if target is None:
            target = _Cluster()
            clusters.append(target)
        elif msg.parent is None:
            msg.parent = _infer_parent(target, msg)
            msg.inferred = msg.parent is not None

        target.add(msg)
        by_id[msg.id] = target
    return clusters


def _infer_parent(cluster, msg):
    previous = cluster.messages
    for candidate in reversed(previous):
        if candidate.sender == msg.sender and candidate.date == msg.date:
            # copie du meme email dans une autre boite mail
            return candidate.parent
    for candidate in reversed(previous):
        if msg.sender in candidate.recipients:
            return candidate.id
    return previous[-1].id


def _flush(group_subject, messages, original_parents, stats, pending_threads):
    if len(messages) < 2:
        return
    for cluster in cluster_group(messages):
        if len(cluster.messages) < 2:
            continue
        dates = [m.date for m in cluster.messages if m.date]
        thread = Thread(
            normalized_subject=group_subject,
            started_at=min(dates) if dates else None,
            last_activity=max(dates) if dates else None,
            email_count=len(cluster.messages),
        )
        pending_threads.append((thread, cluster.messages))
        stats["threads"] += 1
        for msg in cluster.messages:
            if msg.parent != original_parents[msg.id]:
                stats["inferred_replies"] += 1


def _save(pending_threads):
    Thread.objects.bulk_create([t for t, _ in pending_threads], batch_size=1000)
    updates = []
    for thread, messages in pending_threads:
        for msg in messages:
            updates.append(
                Email(
                    id=msg.id,
                    thread_id=thread.id,
                    in_reply_to_id=msg.parent,
                    in_reply_to_inferred=msg.inferred,
                )
            )
    Email.objects.bulk_update(updates, ["thread", "in_reply_to", "in_reply_to_inferred"], batch_size=2000)


def build_threads(log=None):
    """
    Reconstruit toutes les conversations (operation idempotente).
    Renvoie un dict de statistiques.
    """
    log = log or (lambda _msg: None)
    stats = {"header_links": 0, "threads": 0, "inferred_replies": 0}

    with transaction.atomic():
        Email.objects.exclude(thread=None).update(thread=None)
        # On repart de zero pour les liens deduits ; les liens existants
        # (en-tetes, saisie manuelle, anciens imports) sont conserves.
        Email.objects.filter(in_reply_to_inferred=True).update(in_reply_to=None, in_reply_to_inferred=False)
        Thread.objects.all().delete()
        stats["header_links"] = resolve_reply_headers()
        log(f"Liens In-Reply-To resolus: {stats['header_links']}")

        rows = (
            Email.objects.exclude(normalized_subject="")
            .order_by("normalized_subject", "date", "id")
            .values_list("id", "normalized_subject", "date", "from_employee_id", "in_reply_to_id")
            .iterator(chunk_size=BATCH_SIZE)
        )

        batch = []  # [(subject, row)] d'un ou plusieurs sujets complets
        processed = 0

        def process(batch_rows):
            ids = [r[0] for _, r in batch_rows]
            recipients = _recipients_for(ids)
            pending_threads = []
            current_subject, current, original = None, [], {}
            for subject, (eid, _s, date, sender, parent) in batch_rows:
                if subject != current_subject:
                    _flush(current_subject, current, original, stats, pending_threads)
                    current_subject, current, original = subject, [], {}
                current.append(_Msg(eid, date, sender, recipients[eid], parent))
                original[eid] = parent
            _flush(current_subject, current, original, stats, pending_threads)
            _save(pending_threads)

        last_subject = None
        for row in rows:
            subject = row[1]
            if len(batch) >= BATCH_SIZE and subject != last_subject:
                process(batch)
                processed += len(batch)
                log(f"Threads: {processed} emails analyses, {stats['threads']} conversations")
                batch = []
            batch.append((subject, row))
            last_subject = subject
        if batch:
            process(batch)

    return stats
