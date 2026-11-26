from django.db import models
from django.contrib.postgres.search import SearchVectorField
from django.contrib.postgres.indexes import GinIndex


class Employee(models.Model):
    """
    Collaborateur de l'entreprise (une ligne par adresse email).
    """
    name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(unique=True)  # unique =True car chaque employer ne qu'apparaitre une seule fois

    def __str__(self):
        return self.email


class Folder(models.Model):
    """
    Dossier d'une boite mail (inbox, sent, etc.).

    `path` est le chemin relatif dans le maildir (ex: "lay-k/inbox") et
    identifie le dossier de facon unique ; `mailbox` est la boite mail
    proprietaire (ex: "lay-k") et `name` le dernier element du chemin.
    """
    name = models.CharField(max_length=200)
    mailbox = models.CharField(max_length=200, blank=True, db_index=True)
    path = models.CharField(max_length=500, unique=True)

    class Meta:
        ordering = ["path"]

    def __str__(self):
        return self.path or self.name


class Thread(models.Model):
    """
    Conversation reconstruite (commande `build_threads`), a partir des
    en-tetes In-Reply-To quand ils existent, sinon du sujet normalise et des
    participants.
    """
    normalized_subject = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    last_activity = models.DateTimeField(null=True, blank=True)
    email_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-last_activity"]

    def __str__(self):
        return self.normalized_subject or f"Thread {self.pk}"


class Email(models.Model):
    """
    Message email principal.
    """
    # identifiant technique unique provenant du corpus (.eml)
    message_id = models.CharField(max_length=500, unique=True)

    # métadonnées principales
    date = models.DateTimeField(null=True, blank=True)
    subject = models.TextField(blank=True)
    # sujet sans RE:/FW:, en minuscules (sert a regrouper les conversations)
    normalized_subject = models.CharField(max_length=500, blank=True, db_index=True)
    body = models.TextField(blank=True)

    # expéditeur (un seul)
    from_employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="sent_emails",
    )

    # destinataires (To, Cc, Bcc)
    to_employees = models.ManyToManyField(
        Employee,
        related_name="received_emails",
        blank=True,
    )
    cc_employees = models.ManyToManyField(
        Employee,
        related_name="cc_emails",
        blank=True,
    )
    bcc_employees = models.ManyToManyField(
        Employee,
        related_name="bcc_emails",
        blank=True,
    )

    # relation de thread (In-Reply-To)
    in_reply_to = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replies",
    )
    # valeur brute de l'en-tete In-Reply-To, resolue apres l'import
    in_reply_to_header = models.CharField(max_length=500, blank=True)
    # True si in_reply_to a ete deduit par build_threads (et non lu dans l'en-tete)
    in_reply_to_inferred = models.BooleanField(default=False)

    thread = models.ForeignKey(
        Thread,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="emails",
    )

    # dossier de classement
    folder = models.ForeignKey(
        Folder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # champ dédié à la recherche plein texte PostgreSQL
    # (de type tsvector côté PostgreSQL)
    search_vector = SearchVectorField(null=True, blank=True)

    def __str__(self):
        return self.subject[:50] if self.subject else "No subject"

    def save(self, *args, **kwargs):
        if self.subject and not self.normalized_subject:
            from .parsing import normalize_subject

            self.normalized_subject = normalize_subject(self.subject)[:500]
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-date"]
        indexes = [
            # utile pour filtrer/ordonner par date (dashboard, timeline)
            models.Index(fields=["date"]),
            # on garde aussi un index classique sur l'identifiant fonctionnel
            models.Index(fields=["message_id"]),
            # index GIN pour le Full Text Search sur search_vector
            GinIndex(fields=["search_vector"], name="email_fts_gin_idx"),
        ]


class Attachment(models.Model):
    """
    Pièce jointe associée à un email.
    """
    email = models.ForeignKey(
        Email,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    filename = models.CharField(max_length=500)
    content_type = models.CharField(max_length=200, blank=True)
    size = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return self.filename