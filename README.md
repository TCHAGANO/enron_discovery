# Enron Discovery - Projet 2026

Plateforme d'e-Discovery construite avec Django + PostgreSQL pour analyser le corpus Enron (plus de 500 000 emails).

## 1. Contexte et objectif

L'objectif est de transformer un corpus d'emails non structure en application web d'investigation permettant de:

- naviguer dans les echanges,
- rechercher des informations critiques,
- identifier des acteurs cles,
- reconstruire des conversations (threads).

Source des donnees: Enron Email Dataset (CMU)  
https://www.cs.cmu.edu/~enron/

## 2. Stack technique

- Backend et interface: Django 5.2
- Base de donnees: PostgreSQL 16 (Docker)
- Recherche plein texte: PostgreSQL FTS (SearchVector + index GIN)
- Parsing/ingestion: Python (`email`, `re`, `datetime`)
- Environnement Python: `venv`
- Versionnage: Git

## 3. Architecture du projet

```text
enron_discovery/
├── docker-compose.yml
├── manage.py
├── requirements.txt
├── .env.example                           # modele de configuration (copier en .env)
├── scripts/
│   └── parse_email.ipynb                  # exploration Pandas
├── discovery/                             # application Django (label "polls")
│   ├── models.py                          # Employee, Folder, Thread, Email, Attachment
│   ├── parsing.py                         # parsing pur des emails (teste isolement)
│   ├── search.py                          # FTS : indexation, requetes, surlignage
│   ├── threads.py                         # reconstruction des conversations
│   ├── graph.py                           # graphe d'influence + PageRank
│   ├── views.py / urls.py / admin.py
│   ├── management/commands/
│   │   ├── import_enron.py                # import par lots
│   │   ├── build_threads.py               # reconstruit les conversations
│   │   └── rebuild_search_index.py        # recalcule l'index plein texte
│   ├── templates/
│   └── tests/                             # vues, parsing, import, graphe
└── enron_discovery/
    ├── settings.py                        # lit .env / variables d'environnement
    └── urls.py
```

L'application s'appelait `polls` (heritage du tutoriel Django). Le paquet
Python est maintenant `discovery`, mais son label reste `polls` : les tables
(`polls_email`...) et l'historique des migrations des bases existantes sont
conserves, aucune reimportation n'est necessaire.

## 4. Modelisation SQL (MCD logique)

### 4.1 Entites

#### Employee
- `email` (unique)
- `name`

#### Folder
- `path` (unique, ex: `lay-k/inbox`)
- `mailbox` (boite mail proprietaire, ex: `lay-k`)
- `name` (ex: `inbox`)

#### Thread
- `normalized_subject`, `started_at`, `last_activity`, `email_count`

#### Email
- `message_id` (unique)
- `date`
- `subject`
- `body`
- `from_employee` (FK -> Employee)
- `to_employees` (M2M -> Employee)
- `cc_employees` (M2M -> Employee)
- `bcc_employees` (M2M -> Employee)
- `normalized_subject` (sujet sans RE:/FW:, indexe)
- `in_reply_to` (FK recursive -> Email), `in_reply_to_header`, `in_reply_to_inferred`
- `thread` (FK -> Thread)
- `folder` (FK -> Folder)
- `search_vector` (FTS)

#### Attachment
- `email` (FK -> Email)
- `filename`
- `content_type`
- `size`

### 4.2 Contraintes et integrite
- unicite: `Employee.email`, `Email.message_id`
- cles etrangeres: expediteur, dossier, pieces jointes, thread parent
- tables M2M dediees pour To/Cc/Bcc

### 4.3 Optimisation
- index B-tree sur `date`
- index B-tree sur `message_id`
- index GIN sur `search_vector` (FTS)

## 5. Ingestion et parsing

Commande principale: `python manage.py import_enron --path /chemin/vers/maildir`

- parcours recursif du maildir ; le dossier garde la boite mail d'origine (`lay-k/inbox`)
- extraction `From` (et nom complet via `X-From`), `To`, `Cc`, `Bcc`, `Date`, `Subject`, `Message-ID`, `In-Reply-To`
- decodage selon le charset declare, nettoyage du corps (messages cites, signatures, mentions legales ; les blocs "Forwarded by" sont conserves)
- insertion **par lots** (`bulk_create`) avec cache memoire des employes et dossiers : quelques requetes par lot au lieu de plusieurs dizaines par email (environ 18 fois plus rapide sur nos essais)
- index plein texte calcule en une seule requete a la fin (sujet poids A, corps poids B, configuration `english`)
- reconstruction des conversations a la fin (voir ci-dessous)
- import idempotent/incremental : les Message-ID deja en base sont ignores. Sans Message-ID, l'identifiant de repli est le chemin du fichier.

Options : `--batch-size 1000`, `--limit N`, `--skip-threads`, `--skip-index`.

### Reconstruction des conversations

Le corpus Enron ne contient presque jamais d'en-tete `In-Reply-To`.
`python manage.py build_threads` (lance automatiquement apres l'import) :

1. resout les en-tetes `In-Reply-To` en SQL, une fois tout importe (l'ordre d'import n'a plus d'importance) ;
2. regroupe les emails de meme sujet normalise qui partagent au moins un participant et sont espaces de moins de 60 jours ;
3. deduit le parent de chaque reponse : le message precedent dont l'expediteur etait destinataire, sinon le dernier message du fil. Les copies d'un meme email dans plusieurs boites ne sont pas comptees comme des reponses.

La commande est idempotente ; les liens deduits sont marques `in_reply_to_inferred`.

## 6. Installation et execution

### Prerequis

- Python 3.12+
- Docker + Docker Compose

### 1) Creer et activer l'environnement virtuel

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2) Demarrer PostgreSQL (Docker)

```bash
docker compose up -d
docker compose ps
```

### 3) Configurer et appliquer les migrations

```bash
cp .env.example .env      # puis adapter les valeurs
python manage.py migrate
```

### Base deja importee avec l'ancienne version ?

```bash
python manage.py migrate                  # ajoute les nouvelles colonnes, sans reimport
python manage.py rebuild_search_index     # index plein texte en configuration english
python manage.py build_threads            # conversations
```

Les dossiers deja importes gardent leur ancien nom (sans la boite mail) ;
une reimportation dans une base vide est necessaire pour profiter du filtre par boite mail.

### 4) Importer les emails

```bash
python manage.py import_enron --path /chemin/vers/maildir
```

### 5) Lancer le serveur

```bash
python manage.py runserver
```

Configuration : toutes les valeurs sont lues dans `.env` (voir `.env.example`) :
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `POSTGRES_*`,
`STATS_CACHE_SECONDS`, `SEARCH_CONFIG`, `DJANGO_CACHE_LOCATION`.
Les valeurs par defaut correspondent au `docker-compose.yml` (base `enron_db`,
utilisateur `enron`, port hote `5433`). Avec `DJANGO_DEBUG=False`,
`DJANGO_SECRET_KEY` est obligatoire.

## 7. URLs principales

- Dashboard: `http://127.0.0.1:8000/dashboard/`
- Recherche avancee: `http://127.0.0.1:8000/search/`
- Thread detail: `http://127.0.0.1:8000/thread/<email_id>/`
- Graphe d'influence (optionnel): `http://127.0.0.1:8000/influence/`
- Admin Django: `http://127.0.0.1:8000/admin/`

## 8. Fonctionnalites web (cahier des charges)

### Dashboard global
- volume des emails par mois, top 10 expediteurs
- statistiques mises en cache (`STATS_CACHE_SECONDS`, 10 min par defaut)

### Moteur de recherche avancee
- recherche plein texte PostgreSQL, syntaxe type moteur web : `"phrase exacte"`, `OR`, `-exclusion`
- extraits du corps avec les mots trouves surlignes (le contenu des emails reste echappe)
- filtres : expediteur, destinataire (To/Cc/Bcc), boite mail, plage de dates, pieces jointes
- pagination qui conserve les filtres

### Explorateur de threads
- email complet (destinataires, Cc, dossier, pieces jointes)
- conversation reconstruite, en ordre chronologique, indentee selon le message auquel chacun repond

### Graphe d'influence
- graphe interactif (vis-network) des connexions expediteur -> destinataire les plus frequentes (50 a 500)
- taille des noeuds selon le PageRank pondere, epaisseur des aretes selon le nombre d'emails
- classement des acteurs centraux : PageRank, degre, emails envoyes et recus
- filtre par employe ; double-clic sur un noeud pour voir ses emails

## 9. Verification rapide post-import

Compter les emails importes:

```bash
python3 manage.py shell -c "from discovery.models import Email,Employee,Attachment; print('Emails:', Email.objects.count(), 'Employees:', Employee.objects.count(), 'Attachments:', Attachment.objects.count())"
```

Compter les fichiers source:

```bash
find ./maildir -type f | wc -l
```

## 9bis. Qualite des donnees et correction des dates

Le corpus Enron est connu pour contenir des problemes d'integrite (messages rediges, adresses corrigees, en-tetes heterogenes). Pour eviter les dates aberrantes dans les graphiques (ex: annee 2044 issue d'un parsing ambigu), l'import applique une validation simple:

- date invalide ou non parsable => `NULL`
- annee hors plage plausible du corpus (`1990-2010`) => `NULL`

Cette regle est appliquee a l'import et au dashboard mensuel pour conserver des statistiques coherentes.

## 10. Tests

Les tests sont dans `discovery/tests/` :

- `test_views.py` : pages, recherche (FTS, syntaxe, surlignage, filtres, pagination), threads, graphe, cache
- `test_parsing.py` : sujets, adresses, dates, nettoyage du corps
- `test_import.py` : import complet sur un mini maildir, idempotence, reconstruction des threads
- `test_graph.py` : PageRank

```bash
docker compose up -d
python manage.py test discovery -v 2
python manage.py test discovery.tests.test_import -v 2   # un seul module
```

<!-- ## 10. Couverture des objectifs du sujet

- Modelisation SQL normalisee: OK
- Contraintes (FK, unicite): OK
- Optimisation FTS (GIN): OK
- Ingestion/parsing des `.eml`: OK
- Gestion `In-Reply-To`: OK
- Dashboard: OK
- Recherche avancee: OK
- Explorateur de threads: OK
- Graphe d'influence: optionnel, implemente

## 11. Remarques de rendu

- ne pas versionner la base de donnees
- ne pas versionner `maildir/`
- conserver un historique Git propre (commits atomiques)
- inclure une courte modelisation (MCD) dans le document demande -->

## 12. Auteur(s)

Projet realise dans le cadre du cours de Structuration des Donnees (2026).

Redige par [WADE Abdou](https://github.com/WADE57) et [TCHAGANO Bahissou](https://github.com/TCHAGANO).
