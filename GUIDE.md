# Code Expert RAG — OpenFront.io · Guide d'utilisation

Système **RAG agentique quadri-source** qui répond à des questions sur le code
source d'un projet, en combinant 4 bases de connaissance.

**Multi-langage (Sprint 2)** : le pipeline est piloté par un **registre de langages**
(`src/languages/`). Un langage n'est traité que s'il est **VALIDATED** (chunker +
graphe à parité prouvée) ; sinon le projet est **refusé explicitement** — jamais
d'indexation partielle silencieuse. Aujourd'hui **TypeScript / JavaScript = VALIDATED**.
Le **graphe** est produit par **SCIP** (`scip-typescript`) exécuté **en Docker**
→ un **loader unique** vers Neo4j.

> Point d'entrée unique : **`python main.py`** (interface guidée).
> Pour scripter : `python rag.py <commande>`.

---

## 1. Architecture

```
                          ┌──────────────────────────────┐
   Question utilisateur → │   AGENT ReAct (Top-Down)      │  src/agent_react.py
                          │   qwen3-4b-instruct (LM Studio)│
                          └───────────────┬───────────────┘
              fiches d'abord, puis drill-down si nécessaire
        ┌───────────────┬────────────────┬───────────────┐
        ▼               ▼                ▼               ▼
   query_qdrant    query_chroma     query_neo4j     query_json
     _fiches         _chunks          _graph           _db
   (concept)        (code)         (relations)       (config)
        │               │                │               │
   ┌────▼────┐    ┌──────▼─────┐   ┌──────▼─────┐   ┌─────▼─────┐
   │ Qdrant  │    │  ChromaDB  │   │   Neo4j    │   │  SQLite   │
   │ fiches  │    │  chunks    │   │ knowledge  │   │ json_db   │
   │ (240)   │    │  (2100)    │   │ graph      │   │           │
   └─────────┘    └────────────┘   └────────────┘   └───────────┘
        ▲               ▲                ▲               ▲
        └───────────────┴── INDEXATION (ETL) ───────────┘
   audit+GATE → graph(SCIP/Docker) → fiches(LLM) → chunks(tree-sitter) → json
```

> **Backbone graphe = SCIP** : `scip-typescript` tourne dans une image Docker
> (`code-expert-indexers`) et produit un `index.scip`, transformé par un **loader
> unique** (`src/graph_builder/scip_loader.py`, mapping occurrence→arête) en nœuds/arêtes
> Neo4j. Bascule possible vers l'ancien ts-morph via `config.ini` `GRAPH_BACKEND = tsmorph`.

### Modèles & serveurs

| Rôle | Modèle | Serveur | Port |
|---|---|---|---|
| **Génération** (résumés, synthèse, tool-calling) | `qwen3-4b-instruct-2507` (instruct, *sans thinking*) | LM Studio | 1234 |
| **Embeddings** (code, dim 3584) | `nomic-embed-code` 7B (SOTA code) | llama-server (Vulkan, GPU AMD) | 8090 |
| **Graphe** | — | Neo4j (Docker) + indexeurs SCIP (`code-expert-indexers`, Docker) | 7687 / 7474 |
| **Fiches / Chunks** | — | Qdrant (Docker) / Chroma (local) | 6333 / — |
| **Config JSON** | — | SQLite (`json_db.sqlite`) | — |

---

## 2. Prérequis

- **Python 3.12** (système : `C:\Users\admin\AppData\Local\Programs\Python\Python312`).
  Dépendances : `langchain langchain-openai langchain-qdrant langchain-neo4j langchain-chroma
  qdrant-client neo4j chromadb tree-sitter tree-sitter-language-pack protobuf grpcio-tools`.
- **Docker Desktop** (pour Neo4j + Qdrant **+ les indexeurs SCIP**).
- **Image Docker des indexeurs SCIP** (graphe). À construire une fois :
  ```
  docker build -f src/graph_builder/Dockerfile.indexers -t code-expert-indexers:latest .
  ```
  (`doctor` vérifie sa présence ; section « Backbone graphe ».)
- **LM Studio** avec le modèle `qwen3-4b-instruct-2507` téléchargé.
- **llama.cpp** (fourni : `tools/llamacpp/llama-server.exe`, build Vulkan) + le GGUF
  `nomic-embed-code.Q4_K_S.gguf` (chemin dans `config.ini` → `EMBEDDING_GGUF`).

---

## 3. Procédure complète (démarrage à froid)

Le plus simple : **`python main.py`** puis taper **`P`** (Procédure complète). Elle
enchaîne automatiquement les étapes ci-dessous. En détail :

### Étape 1 — Démarrer les services
Depuis `main.py`, option **`S`** (ou inclus dans `P`). Ça :
- démarre **llama-server** (embeddings) dans une fenêtre dédiée si absent ;
- démarre **Neo4j** (`docker compose up -d neo4j`) et **Qdrant** (`docker start qdrant`) ;
- vérifie **LM Studio**.

> ⚠️ **LM Studio doit être lancé à la main** (GUI). Charge `qwen3-4b-instruct-2507`
> avec un **contexte modéré** pour éviter de saturer la VRAM :
> ```
> lms load qwen3-4b-instruct-2507 -c 12288 --parallel 1 --gpu max -y
> ```

### Étape 2 — Diagnostic
Option **`0`** (doctor) : vérifie config, modèles, bases, sources. Doit afficher **0 échec**.

### Étape 3 — Indexation des 4 sources
Option **`A`** (TOUT indexer), dans l'ordre :
1. **Audit + GATE** → `project_manifest_detailed.json` (fichiers pertinents, enrichis
   `language`/`category`/`support_status`). Affiche la **matrice de couverture** ; **STOP**
   si du code non supporté est présent (cf. § Gate).
2. **Graph** → Neo4j via **SCIP/Docker** (`scip-typescript` → loader unique → ~6200 nœuds /
   ~23500 arêtes sur OpenFront)
3. **Fiches** → Qdrant (résumé LLM par fichier + embedding) — **~1 à 2 h**
4. **Chunks** → Chroma (découpage tree-sitter **par langage** + embedding) — ~5-10 min
5. **JSON** → SQLite (fichiers de config)

> L'indexation est **one-shot**. À refaire seulement si le code source change
> ou si tu changes d'embedder (la dimension doit correspondre).

### Étape 4 — Interroger
Option **`I`** : ouvre l'agent en mode tchat. Exemples :
- « Comment fonctionne une attaque entre joueurs ? »
- « Quelles sont les relations de la classe PlayerImpl ? »
- « Où est gérée la connexion WebSocket ? »
- « Quelles dépendances dans package.json ? »

---

## 3 bis. Analyser N'IMPORTE QUEL projet

Option **`M`** du menu (Sources du projet). Trois façons de changer la cible :

1. **Cloner un dépôt git** : colle une URL → le projet est cloné (shallow) **sous
   `code-expert-rag/<nom>`** et `CODE_DIRECTORY` est mis à jour automatiquement.
2. **Pointer un dossier local** : donne un chemin. S'il est déjà sous `code-expert-rag/`,
   il est utilisé tel quel ; sinon il est **copié** sous le root (en excluant
   `node_modules`, `.git`, `dist`…).
3. **Mettre à jour le projet courant** (`git pull`) si c'est un dépôt git → puis ré-index.

Après chaque changement, l'outil **propose de ré-indexer** (les anciens index sont
intégralement remplacés). Le manifest et le graphe de l'ancien projet sont nettoyés.

> **Contrainte** : le projet analysé doit résider **sous `code-expert-rag/`** (le clone
> et la copie le garantissent) — c'est requis par la résolution des chemins relatifs.
>
> **Langages & GATE** : seuls les langages **VALIDATED** (aujourd'hui **TS/JS**) sont
> indexés (graphe SCIP + chunks tree-sitter). À l'audit, une **matrice de couverture**
> classe chaque fichier (CODE/TEXT/CONFIG/IGNORED) et signale tout **code non supporté**
> (ex : `.go`, `.sh`, `.cpp`, `.rb`). Si du code non supporté est présent :
> - en **interactif** (`main.py`), l'outil affiche la liste et **demande** de continuer
>   (n'indexer que TS/JS) ou d'arrêter ;
> - en **script** (`rag.py audit`), il **s'arrête** par défaut — forcer avec
>   `rag.py audit --allow-unsupported` pour n'indexer que le sous-ensemble TS/JS.
>
> Les **fiches** (résumé LLM) et **TEXT** (`.md/.yml`) restent agnostiques. `tsconfig.json`
> optionnel (`--infer-tsconfig`).

---

## 4. Usage rapide (CLI scriptable)

```bash
PY="C:/Users/admin/AppData/Local/Programs/Python/Python312/python"
$PY rag.py doctor          # diagnostic (stdlib, marche sans dépendances)
$PY rag.py doctor --quick  # sans les smoke-tests LLM
$PY rag.py audit|graph|fiches|chunks|json
$PY rag.py query           # agent interactif
```

Serveur d'embeddings seul : `powershell scripts/start_embed_server.ps1`

---

## 5. Carte des fichiers

| Fichier | Rôle |
|---|---|
| `main.py` | **Interface guidée** (services + indexation + agent + procédure) |
| `rag.py` | Orchestrateur CLI + `doctor` (diagnostic stdlib) |
| `config.ini` | Toute la config (endpoints, modèles, bases, GGUF, `GRAPH_BACKEND`) |
| `src/llm_clients.py` | Fabrique des clients LM Studio (chat) / llama-server (embeddings) |
| **`src/languages/`** | **Registre multi-langage** : `base.py` (contrat `LanguageProvider`), `typescript.py`/`javascript.py` (providers), `registry.py` (classification), `gate.py` (matrice + refus) |
| `src/audit_and_filter.py` | Étape 1 — audit & manifest **+ gate** |
| `src/graph_indexer.py` | Étape 2 — chargement Neo4j (dispatch `GRAPH_BACKEND` scip/tsmorph) |
| **`src/graph_builder/`** | **Backbone SCIP** : `Dockerfile.indexers`, `scip_runner.py` (Docker), `scip_loader.py` (SCIP→graphe), `scip.proto`/`scip_pb2.py` ; `ts_kg_analyzer.ts` (rollback ts-morph) |
| `src/metadata_enricher.py` | Étape 3 — fiches Qdrant |
| `src/vectordb_indexer.py` | Étape 4 — chunks Chroma (tree-sitter, **par provider**) |
| `src/json_indexer.py` | Étape 5 — config SQLite |
| `src/agent_react.py` | **Agent ReAct** + 4 outils bornés |
| `tests/` | `test_registry.py`, `test_gate.py`, `golden/` (non-régression chunker) |
| `scripts/start_embed_server.ps1` | Lance le serveur d'embeddings |
| `tools/llamacpp/` | Binaire llama-server (Vulkan) |
| `archive/` | Anciens POCs / scratch (historique, hors pipeline) |

---

## 6. Dépannage (pièges rencontrés & solutions)

| Symptôme | Cause | Solution |
|---|---|---|
| Génération extrêmement lente (minutes/réponse) | **Overcommit VRAM** : contexte LM Studio trop grand × parallel élevé + llama-server sur le même GPU | Recharger : `lms load qwen3-4b-instruct-2507 -c 12288 --parallel 1 --gpu max -y` |
| Tout timeout, même un petit prompt | **Slots LM Studio enlisés** par des générations zombies (tuer le client n'arrête pas le serveur) | `lms unload --all` puis recharger le modèle |
| `nomic-embed-code` → 400 "No models loaded" | LM Studio le détecte en `type=llm` (pas embeddings) | Le servir via **llama-server** (`--embeddings --pooling last`), pas LM Studio |
| Le `file_filter` de l'agent ne trouve rien | Chemins incohérents (antislash vs slash) entre sources | Déjà corrigé : tous les chemins sont en **slashs posix** (`OpenFrontIO/src/...`). Ré-indexer si besoin. |
| `tsconfig.json` ignoré à l'indexation JSON | C'est du **JSONC** (commentaires) → `json.loads` strict échoue | Limitation connue (non bloquant) |
| `rag.py audit` s'arrête : « REFUS : code non supporté » | **Gate** : le projet contient du code d'un langage non VALIDATED (`.go`, `.sh`, `.py`…) | Voulu. Indexer le sous-ensemble TS/JS : `rag.py audit --allow-unsupported` (ou via `main.py`, répondre « oui »). Sinon, valider le langage (Sprint 2). |
| Graphe : « Image Docker 'code-expert-indexers' absente » | Image SCIP non construite | `docker build -f src/graph_builder/Dockerfile.indexers -t code-expert-indexers:latest .` |
| Graphe SCIP vide / 0 nœud | Manifest absent, ou Docker arrêté | Lancer l'audit d'abord ; vérifier Docker Desktop ; `rag.py doctor` (section Backbone graphe) |
| Embeddings/chat OK mais lents au 1er appel | Chargement JIT du modèle | Normal ; pré-charger les modèles évite l'attente |

---

## 7. Pistes d'amélioration (optionnelles)

1. **Reranker** bge-reranker-v2-m3 (installer `sentence-transformers`) → +précision retrieval.
2. **Contextual Retrieval** : préfixer chaque chunk d'un contexte avant l'embedding (gain qualité 2025).
3. **Bascule génération vers le MoE** `qwen3.6-35b-a3b` (plus capable) — un changement de `GENERATION_MODEL` dans `config.ini`.
4. **Déploiement distribué** : embeddings/bases sur le serveur DL325 + GPU dédié, génération à part.
5. **Parser JSONC** pour indexer `tsconfig.json` & co.
6. **Nouveaux langages (Phase 2)** : ajouter un `LanguageProvider` (chunker tree-sitter +
   golden) et un indexeur SCIP Docker (scip-python via Pyright, puis scip-clang, bash) jusqu'à
   parité **VALIDATED**. Le loader SCIP→Neo4j et le gate sont déjà génériques.
7. **Affiner les labels du graphe SCIP** : distinguer `extends`/`implements` (HERITE_DE vs
   IMPLEMENTE) et Class/Interface/Enum (scip-typescript 0.3.15 ne fournit pas `kind`).
