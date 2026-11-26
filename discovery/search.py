"""Recherche plein texte PostgreSQL : indexation et requetes."""

from django.conf import settings
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank, SearchVector
from django.db.models.functions import Left
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import Email

_HL_START = "\x02"
_HL_STOP = "\x03"

# Un tsvector PostgreSQL est limite a 1 Mo. Quelques emails du corpus depassent
# 2 Mo (listes, rapports colles) : on n'indexe que le debut de leur corps.
MAX_INDEXED_BODY_CHARS = 100_000
INDEX_CHUNK_SIZE = 20_000


def search_config():
    return getattr(settings, "SEARCH_CONFIG", "english")


def email_search_vector():
    """Le sujet pese plus lourd (A) que le corps (B) dans le classement."""
    config = search_config()
    return SearchVector(Left("subject", 2000), weight="A", config=config) + SearchVector(
        Left("body", MAX_INDEXED_BODY_CHARS), weight="B", config=config
    )


def update_search_vectors(queryset=None, log=None):
    """
    Calcule search_vector par tranches d'ids (une requete UPDATE par tranche).
    Renvoie le nombre de lignes mises a jour.
    """
    if queryset is None:
        queryset = Email.objects.filter(search_vector__isnull=True)
    ids = list(queryset.order_by("id").values_list("id", flat=True))
    total = 0
    for start in range(0, len(ids), INDEX_CHUNK_SIZE):
        chunk = ids[start : start + INDEX_CHUNK_SIZE]
        total += Email.objects.filter(id__gte=chunk[0], id__lte=chunk[-1], id__in=chunk).update(
            search_vector=email_search_vector()
        )
        if log:
            log(f"Index plein texte: {total}/{len(ids)}")
    return total


def build_query(text):
    """Syntaxe type moteur web : "expression exacte", OR, -exclusion."""
    return SearchQuery(text, search_type="websearch", config=search_config())


def apply_fts(queryset, text):
    query = build_query(text)
    return (
        queryset.filter(search_vector=query)
        .annotate(rank=SearchRank("search_vector", query))
        .order_by("-rank", "-date", "-id")
    )


def headlines_for(email_ids, text):
    """
    {email_id: extrait HTML surligne} calcule uniquement pour les emails de la
    page courante. Le contenu des emails est echappe : seul <mark> est du HTML.
    """
    if not email_ids or not text:
        return {}
    rows = (
        Email.objects.filter(id__in=email_ids)
        .annotate(
            headline=SearchHeadline(
                "body",
                build_query(text),
                config=search_config(),
                start_sel=_HL_START,
                stop_sel=_HL_STOP,
                max_words=35,
                min_words=15,
                max_fragments=2,
                fragment_delimiter=" … ",
            )
        )
        .values_list("id", "headline")
    )
    return {email_id: _to_safe_html(headline) for email_id, headline in rows}


def _to_safe_html(headline):
    if not headline:
        return ""
    if _HL_START not in headline:
        # aucun mot trouve dans le corps (ex: correspondance dans le sujet)
        headline = headline[:300]
    html = str(escape(headline))
    return mark_safe(html.replace(_HL_START, "<mark>").replace(_HL_STOP, "</mark>"))
