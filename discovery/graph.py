"""Graphe d'influence : aretes expediteur -> destinataire et centralite."""

from collections import defaultdict

from django.conf import settings
from django.core.cache import cache

from django.db.models import Count, F, Q

from .models import Email, Employee


def top_edges(limit=100, user=""):
    """
    Les `limit` paires (expediteur, destinataire) les plus frequentes.
    Requete directe sur la table de liaison To, sans passer par Email x M2M.
    Un meme email est souvent present dans plusieurs dossiers (sent,
    sent_items, all_documents...) : on compte les dates d'envoi distinctes
    pour ne compter chaque email qu'une fois.
    Renvoie une liste de dicts {source, target, total} (ids d'employes).
    """
    through = Email.to_employees.through
    qs = through.objects.exclude(employee_id=F("email__from_employee_id"))
    if user:
        qs = qs.filter(Q(email__from_employee__email__icontains=user) | Q(employee__email__icontains=user))
    rows = (
        qs.values(source=F("email__from_employee_id"), target=F("employee_id"))
        .annotate(total=Count("email__date", distinct=True))
        .order_by("-total", "source", "target")[:limit]
    )
    return list(rows)


def pagerank(edges, damping=0.85, iterations=100, tol=1e-9):
    """PageRank pondere (le poids d'une arete = nombre d'emails)."""
    nodes = set()
    out_weight = defaultdict(float)
    incoming = defaultdict(list)
    for e in edges:
        nodes.update((e["source"], e["target"]))
        out_weight[e["source"]] += e["total"]
        incoming[e["target"]].append((e["source"], e["total"]))
    if not nodes:
        return {}
    n = len(nodes)
    rank = dict.fromkeys(nodes, 1.0 / n)
    for _ in range(iterations):
        dangling = sum(rank[v] for v in nodes if out_weight[v] == 0)
        new = {}
        for v in nodes:
            inflow = sum(rank[u] * w / out_weight[u] for u, w in incoming[v])
            new[v] = (1 - damping) / n + damping * (inflow + dangling / n)
        delta = sum(abs(new[v] - rank[v]) for v in nodes)
        rank = new
        if delta < tol:
            break
    return rank


# Le PageRank est calcule sur un graphe large (les RANK_EDGES connexions les
# plus frequentes) : sur les seules aretes affichees, les feuilles d'une etoile
# obtiendraient un score artificiellement eleve.
RANK_EDGES = 5000


def global_metrics():
    """PageRank, emails envoyes/recus et degre sur le graphe large (en cache)."""

    def compute():
        edges = top_edges(limit=RANK_EDGES)
        sent, received, contacts = defaultdict(int), defaultdict(int), defaultdict(set)
        for e in edges:
            sent[e["source"]] += e["total"]
            received[e["target"]] += e["total"]
            contacts[e["source"]].add(e["target"])
            contacts[e["target"]].add(e["source"])
        return {
            "pagerank": pagerank(edges),
            "sent": dict(sent),
            "received": dict(received),
            "degree": {k: len(v) for k, v in contacts.items()},
        }

    return cache.get_or_set("graph:global_metrics", compute, settings.STATS_CACHE_SECONDS)


def build_graph(limit=100, user=""):
    """Donnees pretes pour le template : noeuds, aretes, classement."""
    edges = top_edges(limit=limit, user=user)
    ids = {e["source"] for e in edges} | {e["target"] for e in edges}
    people = {p.id: p for p in Employee.objects.filter(id__in=ids)}
    metrics = global_metrics()
    ranks = {pid: metrics["pagerank"].get(pid, 0.0) for pid in ids}

    # sur le graphe affiche, a defaut de valeur globale
    sent = defaultdict(int)
    received = defaultdict(int)
    contacts = defaultdict(set)
    for e in edges:
        sent[e["source"]] += e["total"]
        received[e["target"]] += e["total"]
        contacts[e["source"]].add(e["target"])
        contacts[e["target"]].add(e["source"])

    ranking = sorted(
        (
            {
                "id": pid,
                "email": people[pid].email,
                "name": people[pid].name,
                "pagerank": ranks.get(pid, 0.0),
                "sent": metrics["sent"].get(pid, sent[pid]),
                "received": metrics["received"].get(pid, received[pid]),
                "degree": metrics["degree"].get(pid, len(contacts[pid])),
            }
            for pid in ids
            if pid in people
        ),
        key=lambda r: (-r["pagerank"], r["email"]),
    )
    max_rank = max(ranks.values(), default=1.0) or 1.0

    nodes = [
        {
            "id": r["id"],
            "label": r["email"].split("@")[0],
            "title": f"{r['email']}\nPageRank: {r['pagerank']:.4f}\nEnvoyes: {r['sent']} | Recus: {r['received']}",
            "value": round(r["pagerank"] / max_rank * 100, 2),
            "highlight": bool(user) and user.lower() in r["email"],
        }
        for r in ranking
    ]
    vis_edges = [
        {"from": e["source"], "to": e["target"], "value": e["total"], "title": f"{e['total']} emails"}
        for e in edges
    ]
    connections = [
        {
            "from_employee__email": people[e["source"]].email,
            "to_employees__email": people[e["target"]].email,
            "total": e["total"],
        }
        for e in edges
        if e["source"] in people and e["target"] in people
    ]
    return {"nodes": nodes, "edges": vis_edges, "ranking": ranking, "connections": connections}
