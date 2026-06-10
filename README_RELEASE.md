# rag_code_expert_ts_js — RAG agentique pour code TypeScript / JavaScript

**Version de référence TS/JS** (état stable « fin de Phase 0 », avant l'introduction
du support Python). Système RAG quadri-source (Neo4j + Qdrant + Chroma + SQLite) avec
agent ReAct. Langages supportés : **TypeScript et JavaScript** (`VALIDATED`) ; tout
autre code (py, go, sh, c++…) est détecté par le **gate** et refusé explicitement
(contournable par `--allow-unsupported` pour n'indexer que TS/JS).

> **Note de provenance** : le dépôt d'origine n'étant pas sous git, cette version a
> été **reconstruite depuis le build courant** en retirant chirurgicalement le support
> Python (provider, registre, golden, image Docker). Elle inclut volontairement les
> correctifs de robustesse **génériques** postérieurs (timeout fiches 120 s/fichier,
> upload Qdrant incrémental, reprise `--resume`) car le bug qu'ils corrigent
> (blocage des fiches à ~80-90 %) existait déjà sur cette base. Tests de conformité :
> registry 30/30, gate 20/20, golden TS/JS 3/3 (byte-identique à la baseline
> pré-refactor).

## Contenu

| Élément | Rôle |
|---|---|
| `rag.py` / `main.py` | orchestrateur CLI / interface guidée |
| `config.ini` | configuration (endpoints, modèles, `GRAPH_BACKEND`, `CODE_DIRECTORY`) |
| `src/languages/` | registre de langages (providers **ts/js**, gate, matrice de couverture) |
| `src/graph_builder/` | backbone graphe SCIP : Dockerfile (**scip-typescript** seul), runner, loader ; `ts_kg_analyzer.ts` = rollback ts-morph (`GRAPH_BACKEND = tsmorph`) |
| `src/*.py` | étapes ETL (audit, graph, fiches, chunks, json) + `agent_react.py` |
| `tests/` | test_registry (30), test_gate (20), golden chunker ts/js (3 fixtures) |
| `GUIDE.md` | installation & utilisation pas-à-pas |
| `DOCUMENTATION_RAG.md` | **fonctionnement interne complet : indexation + agentique** (les passages Python concernent la distribution last_build) |

## Installation rapide

1. **Python 3.12** : `pip install -r requirements.txt`
2. **Docker Desktop** : `docker compose up -d neo4j` · `docker run -d -p 6333:6333 --name qdrant qdrant/qdrant`
3. **Image indexeur SCIP** (graphe) :
   `docker build -f src/graph_builder/Dockerfile.indexers -t code-expert-indexers:latest .`
4. **LM Studio** (génération, :1234) : `lms load qwen3-4b-instruct-2507 -c 12288 --parallel 1 --gpu max -y`
5. **Embeddings** (:8090) : llama.cpp `llama-server` (build Vulkan) + GGUF
   `nomic-embed-code.Q4_K_S.gguf` — adapter les chemins dans
   `scripts/start_embed_server.ps1` et `config.ini` (`EMBEDDING_GGUF`).
   *(Les binaires llama.cpp et les GGUF ne sont pas inclus dans le zip.)*
6. Placer le projet TS/JS à analyser **sous la racine** (ex. `./OpenFrontIO`), pointer
   `CODE_DIRECTORY` dans `config.ini` (ou menu `M` de `main.py`). Pour un dépôt git :
   le node_modules n'est **pas** nécessaire (SCIP résout en interne via tsconfig).
7. `python rag.py doctor` → viser 0 échec, puis `python main.py` → option `P`
   (procédure complète : services → doctor → indexation → agent).

## Vérification

```
python tests/test_registry.py            # 30/30
python tests/test_gate.py                # 20/20
python tests/golden/run_golden.py --check  # 3/3 (ts, js)
python rag.py doctor
```

Voir `DOCUMENTATION_RAG.md` pour le fonctionnement détaillé (pipeline d'indexation,
agent, gate multi-langage) et `GUIDE.md` pour l'exploitation quotidienne.
