# Code Expert RAG — Documentation complète (indexation & agentique)

> Ce document décrit **comment le système indexe un code source** (pipeline ETL
> quadri-source) et **comment l'agent répond aux questions** (agentique ReAct).
> Il complète `GUIDE.md` (qui couvre l'installation et l'utilisation pas-à-pas).


---

## 1. Philosophie & architecture générale

**Principe non négociable : qualité de bout en bout.** Chaque maillon du pipeline est
soit *fort* (optimisé et prouvé pour le langage traité), soit le système **refuse
explicitement** (« je ne peux pas gérer ça »). Il n'existe **aucun fallback générique
dégradé** ni d'indexation partielle silencieuse : la chaîne ne vaut que par son maillon
le plus faible.

Le système combine **4 bases de connaissance** spécialisées, alimentées par un ETL,
et interrogées par un **agent ReAct** :

```
                        ┌────────────────────────────────┐
   Question utilisateur │   AGENT ReAct (Top-Down)        │  src/agent_react.py
                        │   qwen3-4b-instruct (LM Studio) │
                        └───────────────┬─────────────────┘
            fiches d'abord, puis drill-down si nécessaire
      ┌───────────────┬────────────────┬────────────────┐
      ▼               ▼                ▼                ▼
 query_qdrant    query_chroma     query_neo4j      query_json
   _fiches         _chunks          _graph            _db
  (concept)        (code)        (relations)        (config)
      │               │                │                │
 ┌────▼────┐    ┌─────▼──────┐  ┌──────▼─────┐   ┌──────▼────┐
 │ Qdrant  │    │  ChromaDB  │  │   Neo4j    │   │  SQLite   │
 │ fiches  │    │  chunks    │  │ knowledge  │   │ json_db   │
 └─────────┘    └────────────┘  │   graph    │   └───────────┘
                                └────────────┘
      ▲               ▲                ▲                ▲
      └───────────────┴── INDEXATION (ETL) ─────────────┘
  audit+GATE → graphe (SCIP/Docker) → fiches (LLM) → chunks (tree-sitter) → json
```

| Source | Rôle | Contenu | Quand l'agent l'utilise |
|---|---|---|---|
| **Fiches** (Qdrant) | conceptuel | 1 fiche markdown / fichier : résumé LLM + classes/fonctions | **toujours en premier** (orientation) |
| **Chunks** (Chroma) | code réel | découpage AST sémantique du code | voir l'implémentation exacte |
| **Graphe** (Neo4j) | structurel | entités (Class/Method/Function/File) + relations (APPELLE, HERITE_DE…) | « qui appelle quoi », héritage, dépendances |
| **JSON** (SQLite) | configuration | fichiers `.json` (package.json…) | dépendances, config |

### Modèles & serveurs

| Rôle | Modèle | Serveur | Port |
|---|---|---|---|
| Génération (résumés, synthèse, tool-calling) | `qwen3-4b-instruct-2507` (instruct, sans thinking) | LM Studio | 1234 |
| Embeddings (code, **dim 3584**) | `nomic-embed-code` 7B | llama-server (Vulkan) | 8090 |
| Indexeurs SCIP (graphe) | scip-typescript (+ scip-python en last_build) | Docker `code-expert-indexers` | — |

### Convention de chemins (vitale)

Tous les chemins manipulés par le pipeline sont **POSIX (slashs `/`) préfixés du nom du
projet** : `OpenFrontIO/src/core/AttackExecution.ts`. Cette clé est **partagée par les
4 sources** — c'est elle qui permet à l'agent de filtrer les chunks d'un fichier repéré
dans une fiche (`file_filter`), et au loader graphe de filtrer au manifest. Corollaire :
**le projet analysé doit résider sous la racine du dépôt RAG** (`code-expert-rag/<projet>`),
car `relative_path = chemin_fichier.relative_to(racine)`.

---

## 2. Le registre multi-langage et le GATE

### 2.1 Le contrat `LanguageProvider` (`src/languages/base.py`)

Chaque langage est décrit par un objet unique qui paramètre **tout** le pipeline :

```python
@dataclass(frozen=True)
class LanguageProvider:
    name: str                              # "typescript"
    extensions: frozenset[str]             # {".ts", ".tsx"}
    # CHUNKER (tree-sitter)
    ts_language: str                       # grammaire tree-sitter-language-pack
    splittable_node_types: frozenset[str]  # nœuds re-découpables si trop gros
    leaf_node_types: frozenset[str]        # nœuds-feuilles chunkés tels quels
    name_field_node_types: frozenset[str]  # nœuds portant child_by_field_name('name')
    max_lines: int                         # seuil de split (50)
    min_lines: int                         # seuil de merge (10)
    # GATE
    status: "VALIDATED" | "EXPERIMENTAL"   # seul VALIDATED autorise l'indexation
    golden_dir: str                        # fixtures de non-régression
    # GRAPHE
    scip_indexer: ScipIndexerSpec | None   # image/commande Docker
    unwrap_name_node_types: frozenset[str] # nœuds "enveloppe" (ex décorateurs Python)
```

Le **registre** (`src/languages/registry.py`) expose :
- `provider_for_path(path)` / `provider_for_ext(ext)` — résolution extension→provider ;
- `classify_file(path)` → `CODE` / `TEXT` (`.md/.yml`) / `CONFIG` (`.json`) / `UNKNOWN` ;
- `language_support(path)` → `(status, langage)` avec
  `status ∈ {VALIDATED, EXPERIMENTAL, UNSUPPORTED, TEXT, CONFIG, IGNORED}`.
  `UNSUPPORTED` = code reconnu (go, rust, bash, c++…) **sans** provider validé ;
  `IGNORED` = hors périmètre analyse de programme (svg, css, glsl, assets…).

### 2.2 Le GATE (`src/languages/gate.py`)

Exécuté **à l'audit**, avant toute indexation :

1. **Tous** les fichiers scannés (pas seulement les retenus) sont classés → une
   **matrice de couverture** (statut × langage × nombre × exemples) est toujours affichée.
2. Si du code `UNSUPPORTED`/`EXPERIMENTAL` est présent → **alerte détaillée**
   (langages, comptes, chemins) puis **décision** :
   - mode **interactif** (`main.py`) → on **demande à l'humain** : continuer
     (seuls les langages VALIDATED + texte/config seront indexés) ou arrêter ;
   - mode **script** (`rag.py audit`) → **STOP par défaut** (sûr), contournable par
     `--allow-unsupported`.
3. En cas de refus, le STOP intervient **avant l'écriture du manifest** : aucun état
   n'est modifié.

> Décision de design : l'importance d'un code non supporté est jugée par **l'humain**,
> pas par une heuristique — le scoreur de pertinence est biaisé TS/JS (il prend les
> `export VAR=` du shell pour des « exports publics »).

---

## 3. Le pipeline d'indexation (ETL), étape par étape

Ordre d'exécution : `audit → graph → fiches → chunks → json`
(via `python rag.py <étape>` ou `main.py` option `A`).

### 3.1 Étape 1 — Audit + gate (`src/audit_and_filter.py`)

1. **Scan** récursif de `CODE_DIRECTORY` (exclusions : `node_modules`, `.git`, `dist`,
   `build`, caches…). Les extensions admissibles = **celles du registre**
   (`all_extensions()`) + doc (`.md`) + config (`.json/.yml`).
2. **Scoring de pertinence** par fichier (heuristiques : exports, documentation,
   mots-clés structurels, taille, répertoire core…). Filtre `score >= 5`
   (les `README.md` sont toujours conservés).
3. **GATE** (cf. §2.2) sur l'ensemble des fichiers vus.
4. Sortie : `project_manifest_detailed.json` — la **source de vérité** de toutes les
   étapes suivantes. Chaque entrée porte : `path`, `relative_path`, `score`, `reasons`,
   `lines`, `size` + enrichissement multi-langage `category` / `language` /
   `support_status`.

### 3.2 Étape 2 — Graphe de connaissance (SCIP → Neo4j)

Le backbone graphe est **SCIP** (Sourcegraph Code Intelligence Protocol) : des indexeurs
compilateur-grade produisent un index binaire d'**occurrences de symboles** résolues,
transformé par **un loader unique** en graphe Neo4j. (`config.ini` :
`GRAPH_BACKEND = scip` ; `tsmorph` reste disponible en rollback TS/JS.)

**Pourquoi SCIP ?** Mesuré sur OpenFront : plus de types détectés, ~2,5× plus de
références résolues que ts-morph, scoping exact, zéro « nœud fantôme ». Et le même
modèle de données couvre tous les langages (un indexeur par langage, un seul loader).

#### a) Exécution de l'indexeur (`src/graph_builder/scip_runner.py`)

Les indexeurs SCIP tournent **obligatoirement dans Docker/Linux** (scip-python est
cassé sur Windows natif). Image : `code-expert-indexers` (cf.
`src/graph_builder/Dockerfile.indexers`, base `node:20-slim`) :

```
docker run --rm -v <PROJET>:/work -v <OUT>:/out -w /work code-expert-indexers:latest \
    scip-typescript index --output /out/index.scip --no-progress-bar
```

- TS/JS : `scip-typescript` (utilise `tsconfig.json`, sinon `--infer-tsconfig`).
  Fonctionne **sans** `node_modules` (la résolution interne au projet suffit).
- Python (last_build) : `scip-python` (moteur Pyright). Requiert un
  `pyrightconfig.json` (créé par défaut `{"include": ["."]}` si absent).
- Le **langage primaire** du projet est lu dans le manifest (langue VALIDATED
  majoritaire) → `run_scip_for_language()` choisit l'indexeur.

#### b) Le loader unique (`src/graph_builder/scip_loader.py`)

Parse `index.scip` (protobuf `scip.proto` → `scip_pb2.py`) et applique **une règle de
mapping unique**, valable pour tous les langages :

- **Symboles** : un symbole SCIP est une chaîne hiérarchique du type
  `scip-typescript npm proj HEAD src/`File.ts`/Classe#methode().`
  Le **suffixe du dernier descripteur** donne le type d'entité :
  `#` = type (classe/interface), `().` = méthode/fonction, `.` = champ/terme (ignoré),
  `(...)` = paramètre (ignoré), `local N` = variable locale (ignoré).
- **Nœuds** : chaque **définition** (occurrence avec rôle `Definition`, non-locale,
  dans un fichier du **manifest**) devient un nœud `Class` / `Interface` / `Method` /
  `Function`. Les nœuds `File` proviennent du manifest.
- **Arêtes** :
  - `DEFINIT_DANS` : méthode → type propriétaire (lu dans la hiérarchie du symbole) ;
  - `APPELLE` / `UTILISE` : chaque **référence** située dans l'`enclosing_range`
    (le corps) d'une définition conteneur F crée `F → cible`
    (`APPELLE` si la cible est méthode/fonction, `UTILISE` si c'est un type).
    Le conteneur retenu est le plus **interne** (innermost) ;
  - `IMPLEMENTE` / `HERITE_DE` : relations `is_implementation` de SCIP.
    **Spécificité langage auto-détectée** (via le scheme du symbole) : en TypeScript la
    cible est une `Interface` (IMPLEMENTE) ; en Python il n'y a pas d'interfaces → tout
    `#` est une `Class` et ces relations deviennent `HERITE_DE` (héritage, y compris multiple).
- **Filtrage** : tout est limité au périmètre du manifest ; les symboles externes non
  résolus et `local` sont ignorés (équivalent du skip `node_modules`).

#### c) Chargement Neo4j (`src/graph_indexer.py`, classe `CodeGraphBuilder`)

`clear_database()` → contraintes d'unicité sur `id` par label → chargement **batch**
(`UNWIND` paramétré) des nœuds puis des arêtes. Le graphe est aussi écrit dans
`knowledge-graph.json` (`{nodes, edges}`) — **consommé par l'étape fiches**.

Schéma Neo4j résultant :

```
(:Class|Interface|Function|Method|File {id, name, filePath, line})
(:Method)-[:DEFINIT_DANS]->(:Class)
(:Method|Function)-[:APPELLE {callLine}]->(:Method|Function)
(:Method|Function)-[:UTILISE {refLine}]->(:Class|Interface)
(:Class)-[:IMPLEMENTE]->(:Interface)        # TS
(:Class)-[:HERITE_DE]->(:Class)             # Python (et TS extends à terme)
```

Ordres de grandeur mesurés : OpenFront (TS, 478 fichiers) = 6 215 nœuds / 23 568 arêtes ;
click (Python, 42 fichiers) = 613 nœuds / 1 553 arêtes.

### 3.3 Étape 3 — Fiches conceptuelles (LLM → Qdrant) (`src/metadata_enricher.py`)

Pour **chaque fichier** du graphe (nœuds `File`/`Config` de `knowledge-graph.json`) :

1. Lecture du code source complet (tronqué à 400 lignes pour le prompt).
2. **Résumé LLM** « éditorial détaillé » (rôle principal + structure technique),
   généré par `qwen3-4b-instruct` (max_tokens 1200, temp 0.1).
3. **Fiche markdown normalisée** : `# Fiche : <chemin>` + résumé + listes des
   classes/fonctions extraites du graphe (cross-référence structurelle).
4. **Embedding** de la fiche (`nomic-embed-code`, dim 3584) → point **Qdrant**
   (collection `code_metadata`) avec payload `{relative_path, summary, fiche_md,
   main_classes, main_functions}`.

**Résilience (intégrée après diagnostic d'un blocage récurrent à ~80-90 %)** :
- chaque appel LLM a un **timeout de 120 s** (+1 retry) — une génération qui reste à
  0 % (dégradation VRAM cumulative) lève une erreur au lieu de figer le pipeline ;
- boucle **par fichier** instrumentée (log : chemin, lignes, taille prompt, durée) ;
  un fichier en échec reçoit une fiche de fallback, le run **continue** ;
- **upload Qdrant incrémental par lot de 8** — un crash à 90 % conserve les 90 % ;
- **reprise** : `python rag.py fiches --resume` conserve la collection et **saute**
  les fichiers déjà indexés (comparaison sur `relative_path`).

C'est l'étape la plus longue (~30-80 s/fichier, séquentiel pour éviter le wedge des
slots LM Studio).

### 3.4 Étape 4 — Chunks de code (tree-sitter → Chroma) (`src/vectordb_indexer.py`)

Découpage **cAST** (split/merge sémantique sur l'AST), paramétré **par provider** :

1. **Routage** : `classify_file()` → CODE = chunker AST du langage ; TEXT (`.md/.yml`)
   = `RecursiveCharacterTextSplitter` (512/50) ; CONFIG/UNKNOWN = ignorés ici.
2. **Split** (`RecursiveASTSplitter`) : descente récursive de l'AST tree-sitter.
   - nœud dans `splittable_node_types` (classe, fonction, if/for/try…) **et** > 50
     lignes → on descend dans son `body` (re-découpe en méthodes/blocs) ;
   - nœud ≤ 50 lignes ou `leaf_node_types` (imports, champs, commentaires…)
     → **chunk atomique** ;
   - chaque chunk porte un **nom hiérarchique** (`Fichier.Classe.methode`) construit
     via `name_field_node_types` (et `unwrap_name_node_types` pour les enveloppes,
     ex. `decorated_definition` Python → nom de la fonction décorée) ;
   - les **JSDoc** attenants sont capturés et attachés au chunk (TS/JS).
3. **Merge** (`ChunkMerger`) : les chunks courts (< 10 lignes) **consécutifs et de même
   parent** sont fusionnés en `merged_block` (max 50 lignes) — évite la fragmentation.
4. **Formatage pour l'embedding** : `// Documentation:` (JSDoc nettoyée) +
   `// Context: File: <chemin> | Chunk: <nom hiérarchique>` + code source. Le contexte
   préfixé améliore nettement le retrieval.
5. Stockage **Chroma** (`chroma_db_code/`), métadonnées : `source_file` (clé posix
   partagée), `hierarchical_name`, `chunk_type`, `start_line`, `end_line`.

**Non-régression** : la fonction `chunk_source(provider, content, name)` est la source
unique partagée par l'indexeur **et** le harnais golden
(`tests/golden/run_golden.py --check|--regen`) — chaque langage a des fixtures et des
sorties attendues figées ; toute dérive du chunker casse le golden.

### 3.5 Étape 5 — Config JSON (SQLite) (`src/json_indexer.py`)

Les `.json` du manifest sont stockés dans `json_db.sqlite` (table `json_files` :
`relative_path`, `content`). Limitation connue : les JSONC (`tsconfig.json` avec
commentaires) sont ignorés (parse strict).

### 3.6 Artefacts produits

| Fichier | Produit par | Consommé par |
|---|---|---|
| `project_manifest_detailed.json` | audit | graph, fiches (indirect), chunks, json |
| `.scip_out/index.scip` | scip_runner (Docker) | scip_loader |
| `knowledge-graph.json` | graph (loader) | **fiches** (entités par fichier) |
| Neo4j (bolt :7687) | graph | agent (outil graphe) |
| Qdrant `code_metadata` (:6333) | fiches | agent (outil fiches) |
| `chroma_db_code/` | chunks | agent (outil chunks) |
| `json_db.sqlite` | json | agent (outil json) |

> ⚠️ Un changement d'**embeddeur** (dimension ≠ 3584) impose de ré-indexer fiches+chunks.

---

## 4. L'agentique (`src/agent_react.py`)

### 4.1 Architecture de l'agent

- **Pattern** : agent **ReAct** construit avec `langchain.agents.create_agent`
  (LangChain v1 / LangGraph), checkpointer `InMemorySaver` (mémoire de conversation
  par `thread_id`), `recursion_limit = 12` (borne le nombre de tours outil).
- **Modèle** : `qwen3-4b-instruct` (tool-calling + synthèse, temp 0) via
  `llm_clients.get_chat()` ; embeddings de requête via `get_embeddings()` —
  enveloppe `_PrefixedEmbeddings` qui applique le préfixe de tâche nomic
  (`"Represent this query for searching relevant code:"`) **aux requêtes uniquement**
  (les documents sont embarqués bruts).
- **Stratégie Top-Down imposée par le system prompt** :
  1. **Toujours** commencer par `query_qdrant_fiches` (orientation conceptuelle —
     les fiches donnent le rôle des fichiers et leurs chemins) ;
  2. répondre directement si les fiches suffisent ;
  3. sinon **drill-down** ciblé : chunks (code), graphe (relations), json (config).
- **Anti-hallucination** : interdiction de répondre sans contexte d'outil ; citation
  des sources (fichier, « selon le graphe ») ; si rien de pertinent → réponse
  explicite « Aucune information pertinente trouvée dans la base de code indexée. »

### 4.2 Les 4 outils — tous BORNÉS

Le dimensionnement (top-k petits + troncatures) est délibéré : il garantit que le
contexte du modèle (12 288 tokens) **ne sature jamais**, quel que soit le nombre de
tours.

| Outil | Source | Bornes | Détail |
|---|---|---|---|
| `query_qdrant_fiches(query)` | Qdrant | k=3, 700 car./fiche | recherche sémantique sur les fiches ; renvoie `# Fiche : <chemin>` + contenu tronqué |
| `query_chroma_chunks(query, file_filter?)` | Chroma | k=4, 800 car./chunk | extraits de code réels ; `file_filter` = liste de chemins **posix** (`$in` sur `source_file`) pour cibler les fichiers repérés via les fiches |
| `query_neo4j_graph(question)` | Neo4j | LIMIT 15, 6 rel/sens, 1500 car. | 1) extraction des **noms d'entités** de la question par LLM (`with_structured_output`, schéma pydantic `_Entities`) ; 2) Cypher compact : `MATCH (n) WHERE n.name = nm` + relations sortantes/entrantes agrégées |
| `query_json_db(query)` | SQLite | 3 fichiers, 600 car. | LIKE sur chemin/contenu ; jamais de dump complet |

Le chaînage typique observé : *« Comment fonctionne AttackExecution ? »* →
fiches (repère `OpenFrontIO/src/core/execution/AttackExecution.ts`) → chunks avec
`file_filter` sur ce chemin → synthèse sourcée. *« Qui hérite de ParamType ? »* →
fiches → graphe (extraction `ParamType` → Cypher → arêtes HERITE_DE entrantes).

### 4.3 Interfaces

- `python rag.py query` ou `main.py` option `I` → boucle interactive (streaming).
- API programmatique : `from src.agent_react import query; query("...", thread_id="x")`.

---

## 5. Ajouter un langage (checklist Sprint 2)

1. **Provider** `src/languages/<lang>.py` : extensions, grammaire tree-sitter,
   node-types (splittable/leaf/name_field/unwrap) **vérifiés par introspection de la
   grammaire réelle**, `status="EXPERIMENTAL"`.
2. Enregistrer dans `registry.py` (`_PROVIDERS`).
3. **Golden chunker** : fixtures représentatives dans `tests/golden/<lang>/fixtures/`
   (classe à splitter, fonction autonome, blocs à merger, spécificités du langage),
   `run_golden.py --regen` puis **inspection manuelle** (aucune unité logique coupée),
   puis `--check` en CI.
4. **Indexeur SCIP** : étendre `Dockerfile.indexers` + `scip_runner.py`
   (`run_scip_<lang>` + dispatch). Vérifier le format des symboles/`enclosing_range`
   du nouvel indexeur ; adapter `scip_loader` **uniquement** si la sémantique diffère
   (ex. interfaces vs héritage — auto-détecté par scheme).
5. **Vertical réel** : indexer un vrai repo du langage (audit→graph→fiches→chunks),
   spot-checks du graphe (membres d'une classe, appels résolus, héritage), questions
   agent sourcées correctes.
6. Promouvoir `status="VALIDATED"` + ajuster les tests (`test_registry`, `test_gate`).

Definition of Done par langage : **0 fichier ignoré en silence · golden chunker vert ·
graphe résolu à précision comparable TS/JS · réponses agent sourcées correctes.**

---

## 6. Opérations & dépannage

### Démarrage des services

```
docker compose up -d neo4j          # Neo4j (bolt :7687, neo4j/password) — PAS le service "api"
docker start qdrant                 # Qdrant (:6333)
powershell scripts/start_embed_server.ps1     # llama-server embeddings (:8090)
lms load qwen3-4b-instruct-2507 -c 12288 --parallel 1 --gpu max -y   # LM Studio (:1234)
docker build -f src/graph_builder/Dockerfile.indexers -t code-expert-indexers:latest .
```

`python rag.py doctor` vérifie tout (config, modèles, bases, sources, couverture
langages, image SCIP) — viser **0 échec**.

### Commandes

```
python rag.py audit [--allow-unsupported]   # manifest + gate
python rag.py graph                          # SCIP → Neo4j (+ knowledge-graph.json)
python rag.py fiches [--resume]              # Qdrant (long ; reprise possible)
python rag.py chunks                         # Chroma
python rag.py json                           # SQLite
python rag.py query                          # agent interactif
python main.py                               # interface guidée (services, sources, tout)
```

### Pièges connus (tous vécus)

| Symptôme | Cause | Solution |
|---|---|---|
| Génération très lente / figée à 0 % | overcommit VRAM (contexte LM Studio trop grand × parallel, + embeddeur 7B sur le même GPU) | recharger : `lms load qwen3-4b-instruct-2507 -c 12288 --parallel 1 --gpu max -y` ; le timeout 120 s des fiches transforme désormais le figeage en erreur récupérable |
| Tout timeout après un kill de run | slots LM Studio « wedgés » par des générations zombies | `lms unload --all` puis recharger |
| Fiches mortes en plein run | (historique) blocage sans timeout + upload tout-ou-rien | corrigé : timeout/fichier + upload incrémental + `--resume` |
| `nomic-embed-code` → 400 « No models loaded » | LM Studio le détecte en type=llm | le servir via **llama-server** (`--embeddings --pooling last`) |
| `file_filter` agent ne matche rien | chemins non posix / préfixe absent | convention §1 ; ré-indexer |
| audit s'arrête « REFUS : code non supporté » | gate (voulu) | `--allow-unsupported` ou valider le langage |
| graphe : image Docker absente | image non construite | `docker build -f src/graph_builder/Dockerfile.indexers -t code-expert-indexers:latest .` |
| scip-python sous Windows natif | bug path.sep | **toujours** via Docker (c'est le design) |

### Limites connues (assumées)

- TS : `extends` est classé `IMPLEMENTE` (HERITE_DE=0) et enums/type-alias étiquetés
  `Class` — scip-typescript 0.3.15 ne remplit pas `kind` ; cosmétique (l'agent matche
  par nom).
- JSONC non indexé en étape json.
- Fiches : ~30-80 s/fichier (génération locale séquentielle).
