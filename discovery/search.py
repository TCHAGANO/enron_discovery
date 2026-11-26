"""Recherche plein texte PostgreSQL : indexation et requetes."""

from django.conf import settings
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank, SearchVector
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import Email

_HL_START = "\x02"
_HL_STOP = "\x03"


def search_config():
    return getattr(settings, "SEARCH_CONFIG", "english")


def email_search_vector():
    """Le sujet pese plus lourd (A) que le corps (B) dans le classement."""
    config = search_config()
    return SearchVector("subject", weight="A", config=config) + SearchVector("body", weight="B", config=config)


def update_search_vectors(queryset=None):
    """Calcule search_vector en une seule requete UPDATE. Renvoie le nombre de lignes."""
    if queryset is None:
        queryset = Email.objects.filter(search_vector__isnull=True)
    return queryset.update(search_vector=email_search_vector())


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
