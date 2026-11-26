import hashlib

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404, render
from django.utils.dateparse import parse_date

from .graph import build_graph
from .models import Attachment, Email, Employee, Folder
from .parsing import MAX_VALID_YEAR, MIN_VALID_YEAR
from .search import apply_fts, headlines_for

SEARCH_PAGE_SIZE = 20
GRAPH_LIMITS = (50, 100, 200, 500)


def _cached(key, compute):
    return cache.get_or_set(key, compute, settings.STATS_CACHE_SECONDS)


def _global_counts():
    return _cached(
        "stats:counts",
        lambda: {"total_emails": Email.objects.count(), "total_employees": Employee.objects.count()},
    )


def _top_senders(limit):
    return _cached(
        f"stats:top_senders:{limit}",
        lambda: list(
            Email.objects.values("from_employee__email")
            .annotate(total=Count("id"))
            .order_by("-total", "from_employee__email")[:limit]
        ),
    )


def _paginate_without_count(qs, page_str, per_page=SEARCH_PAGE_SIZE):
    """Pagination sans COUNT(*) : on lit une ligne de plus pour savoir s'il y a une suite."""
    try:
        page = max(int(page_str or 1), 1)
    except (TypeError, ValueError):
        page = 1
    start = (page - 1) * per_page
    rows = list(qs[start : start + per_page + 1])
    emails = rows[:per_page]
    return {
        "emails": emails,
        "current_count": len(emails),
        "current_page": page,
        "has_previous": page > 1,
        "previous_page": page - 1,
        "has_next": len(rows) > per_page,
        "next_page": page + 1,
    }


def _result_label(pagination, per_page=SEARCH_PAGE_SIZE):
    shown_until = (pagination["current_page"] - 1) * per_page + pagination["current_count"]
    return f"{shown_until}+" if pagination["has_next"] else shown_until


# --- Accueil ---
def home(request):
    context = {**_global_counts(), "top_senders": _top_senders(5)}
    return render(request, "home.html", context)


# --- Dashboard ---
def _monthly_volume():
    rows = (
        Email.objects.exclude(date__isnull=True)
        .filter(date__year__gte=MIN_VALID_YEAR, date__year__lte=MAX_VALID_YEAR)
        .annotate(month=TruncMonth("date"))
        .values("month")
        .annotate(total=Count("id"))
        .order_by("month")
    )
    return [(row["month"].strftime("%b %Y"), row["total"]) for row in rows if row["month"]]


def dashboard(request):
    monthly = _cached("stats:monthly", _monthly_volume)
    context = {
        **_global_counts(),
        "chart_labels": [label for label, _ in monthly],
        "chart_values": [value for _, value in monthly],
        "top_senders": _top_senders(10),
    }
    return render(request, "dashboard.html", context)


# --- Recherche avancee (FTS PostgreSQL) ---
def _mailboxes():
    return _cached(
        "stats:mailboxes",
        lambda: list(
            Folder.objects.exclude(mailbox="").order_by("mailbox").values_list("mailbox", flat=True).distinct()
        ),
    )


def _recipient_filter(value):
    condition = Q()
    for field in (Email.to_employees, Email.cc_employees, Email.bcc_employees):
        through = field.through
        condition |= Q(
            Exists(through.objects.filter(email_id=OuterRef("pk"), employee__email__icontains=value))
        )
    return condition


def _safe_date(value):
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:  # format correct mais date impossible (ex: 2001-13-45)
        return None


def search_emails(request):
    params = request.GET
    q = params.get("q", "").strip()
    sender = params.get("sender", "").strip()
    recipient = params.get("recipient", "").strip()
    mailbox = params.get("mailbox", "").strip()
    date_from = params.get("date_from", "").strip()
    date_to = params.get("date_to", "").strip()
    has_attachments = params.get("attachments") == "1"

    errors = []
    parsed_from = _safe_date(date_from)
    parsed_to = _safe_date(date_to)
    if date_from and parsed_from is None:
        errors.append("Date de debut invalide.")
    if date_to and parsed_to is None:
        errors.append("Date de fin invalide.")

    qs = Email.objects.select_related("from_employee", "folder")
    if sender:
        qs = qs.filter(from_employee__email__icontains=sender)
    if recipient:
        qs = qs.filter(_recipient_filter(recipient))
    if mailbox:
        qs = qs.filter(folder__mailbox=mailbox)
    if parsed_from:
        qs = qs.filter(date__date__gte=parsed_from)
    if parsed_to:
        qs = qs.filter(date__date__lte=parsed_to)
    if has_attachments:
        qs = qs.filter(Exists(Attachment.objects.filter(email_id=OuterRef("pk"))))

    qs = apply_fts(qs, q) if q else qs.order_by("-date", "-id")

    pagination = _paginate_without_count(qs, params.get("page"))
    snippets = headlines_for([e.id for e in pagination["emails"]], q)
    for e in pagination["emails"]:
        e.snippet = snippets.get(e.id, "")

    query_without_page = params.copy()
    query_without_page.pop("page", None)
    has_filters = any([q, sender, recipient, mailbox, date_from, date_to, has_attachments])

    context = {
        **pagination,
        "q": q,
        "sender": sender,
        "recipient": recipient,
        "mailbox": mailbox,
        "mailboxes": _mailboxes(),
        "date_from": date_from,
        "date_to": date_to,
        "has_attachments": has_attachments,
        "errors": errors,
        "base_query": query_without_page.urlencode(),
        "total_results": _result_label(pagination),
        "search_hint": "" if has_filters else "Affichage de tous les emails (pagine). Ajoutez des filtres pour affiner.",
        "enable_pagination": True,
    }
    return render(request, "search.html", context)


# --- Detail d'un thread ---
MAX_FALLBACK_DEPTH = 50


def _fallback_conversation(selected):
    """Sans thread reconstruit : on remonte et redescend les liens In-Reply-To."""
    root = selected
    seen = {root.id}
    while root.in_reply_to_id and root.in_reply_to_id not in seen and len(seen) < MAX_FALLBACK_DEPTH:
        root = Email.objects.select_related("from_employee").get(id=root.in_reply_to_id)
        seen.add(root.id)

    conversation = [(root, 0)]

    def walk(parent, depth):
        if depth > MAX_FALLBACK_DEPTH:
            return
        for child in parent.replies.select_related("from_employee").order_by("date", "id"):
            conversation.append((child, depth))
            walk(child, depth + 1)

    walk(root, 1)
    return root, conversation


def _thread_conversation(selected):
    emails = list(
        Email.objects.filter(thread_id=selected.thread_id)
        .select_related("from_employee")
        .prefetch_related("to_employees")
        .order_by("date", "id")
    )
    # une seule copie par email (meme expediteur, meme date), en gardant
    # celle qui est affichee
    def copy_key(e):
        return (e.from_employee_id, e.date) if e.date else ("id", e.id)

    copies = {}
    for e in emails:
        if copy_key(e) not in copies or e.id == selected.id:
            copies[copy_key(e)] = e
    kept_id = {e.id: copies[copy_key(e)].id for e in emails}  # copie -> copie affichee
    emails = [e for e in emails if kept_id[e.id] == e.id]
    depth = {}
    ids = {e.id for e in emails}
    for e in emails:
        parent = kept_id.get(e.in_reply_to_id, e.in_reply_to_id)
        depth[e.id] = depth.get(parent, -1) + 1 if parent in ids else 0
    return emails[0], [(e, min(depth[e.id], 8)) for e in emails]


def thread_detail(request, email_id):
    selected = get_object_or_404(
        Email.objects.select_related("from_employee", "folder", "thread").prefetch_related(
            "to_employees", "cc_employees", "attachments"
        ),
        id=email_id,
    )
    if selected.thread_id:
        root, items = _thread_conversation(selected)
    else:
        root, items = _fallback_conversation(selected)

    for email_obj, depth in items:
        email_obj.depth = depth
    context = {
        "selected": selected,
        "root": root,
        "conversation": [e for e, _ in items],
    }
    return render(request, "thread_detail.html", context)


# --- Graphe d'influence ---
def influence_graph(request):
    user_email = request.GET.get("user", "").strip().lower()
    try:
        limit = int(request.GET.get("limit", 100))
    except ValueError:
        limit = 100
    if limit not in GRAPH_LIMITS:
        limit = 100

    key = "graph:%d:%s" % (limit, hashlib.md5(user_email.encode()).hexdigest())
    graph = _cached(key, lambda: build_graph(limit=limit, user=user_email))
    context = {
        "connections": graph["connections"],
        "ranking": graph["ranking"][:20],
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "user_email": user_email,
        "limit": limit,
        "limits": GRAPH_LIMITS,
    }
    return render(request, "influence.html", context)


# --- Liste simple de tous les emails ---
def email_list(request):
    emails_list = Email.objects.select_related("from_employee").order_by("-date", "-id")
    paginator = Paginator(emails_list, 50)
    emails = paginator.get_page(request.GET.get("page"))
    # nombre de destinataires calcule en une requete pour la page
    counts = dict(
        Email.to_employees.through.objects.filter(email_id__in=[e.id for e in emails])
        .values("email_id")
        .annotate(n=Count("id"))
        .values_list("email_id", "n")
    )
    for e in emails:
        e.recipient_count = counts.get(e.id, 0)
    return render(request, "email_list.html", {"emails": emails, "stats": _global_counts()})
