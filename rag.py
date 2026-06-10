#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rag.py - Orchestrateur RAG "Code Expert" (jalon LM Studio).

Point d'entree unique pour lancer ET debugger les differentes etapes du RAG.

Sous-commandes :
    doctor   Diagnostique complet de l'environnement (stdlib pur, aucune dep requise)
    audit    Etape 1 : audit + manifest filtre
    graph    Etape 2 : indexation Neo4j (knowledge graph)
    fiches   Etape 3 : indexation Qdrant (fiches conceptuelles)
    chunks   Etape 4 : indexation des chunks de code
    json     Etape 5 : indexation des fichiers .json (SQLite)
    query    Etape 6 : interroger le code (agent)
    menu     Menu interactif (defaut si aucun argument)

Le `doctor` ne depend que de la stdlib : il fonctionne meme si le venv/les
dependances ML ne sont pas (encore) installes. Les autres etapes importent
leurs dependances de maniere paresseuse et signalent clairement ce qui manque.

Usage :
    python rag.py doctor
    python rag.py doctor --quick      # saute les smoke-tests LLM (lents : chargement JIT)
    python rag.py audit
    python rag.py                       # -> menu interactif
"""

from __future__ import annotations

import argparse
import configparser
import json
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.ini"

# --------------------------------------------------------------------------- #
#  Affichage (ASCII safe pour la console Windows)                             #
# --------------------------------------------------------------------------- #
OK = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"
INFO = "[ .. ]"


def line(char: str = "-", n: int = 64) -> None:
    print(char * n)


def title(txt: str) -> None:
    print()
    line("=")
    print(f"  {txt}")
    line("=")


# --------------------------------------------------------------------------- #
#  Config                                                                      #
# --------------------------------------------------------------------------- #
def load_config() -> configparser.SectionProxy | None:
    if not CONFIG_PATH.exists():
        print(f"{FAIL} config.ini introuvable : {CONFIG_PATH}")
        return None
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH, encoding="utf-8")
    return cfg["DEFAULT"]


# --------------------------------------------------------------------------- #
#  Helpers HTTP (stdlib)                                                       #
# --------------------------------------------------------------------------- #
def http_get(url: str, timeout: float = 6.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def http_post_json(url: str, payload: dict, timeout: float = 120.0,
                   api_key: str = "lm-studio") -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def tcp_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
#  DOCTOR - checks individuels                                                 #
# --------------------------------------------------------------------------- #
class Doctor:
    def __init__(self, cfg, quick: bool = False):
        self.cfg = cfg
        self.quick = quick
        self.passed = 0
        self.failed = 0
        self.warned = 0

    def ok(self, msg: str):
        self.passed += 1
        print(f"{OK} {msg}")

    def bad(self, msg: str):
        self.failed += 1
        print(f"{FAIL} {msg}")

    def warn(self, msg: str):
        self.warned += 1
        print(f"{WARN} {msg}")

    # --- 1. Python & config ------------------------------------------------ #
    def check_python(self):
        v = sys.version_info
        if v >= (3, 10):
            self.ok(f"Python {v.major}.{v.minor}.{v.micro}")
        else:
            self.bad(f"Python {v.major}.{v.minor} (>=3.10 requis)")

    def check_config(self):
        required = ["LLM_BASE_URL", "GENERATION_MODEL", "EMBEDDING_MODEL",
                    "NEO4J_URI", "QDRANT_HOST", "QDRANT_PORT", "CODE_DIRECTORY"]
        missing = [k for k in required if k not in self.cfg]
        if missing:
            self.bad(f"config.ini : cles manquantes -> {missing}")
        else:
            self.ok("config.ini : toutes les cles essentielles presentes")
            print(f"       LLM_BASE_URL    = {self.cfg['LLM_BASE_URL']}")
            print(f"       GENERATION_MODEL= {self.cfg['GENERATION_MODEL']}")
            print(f"       EMBEDDING_MODEL = {self.cfg['EMBEDDING_MODEL']}")

    # --- 2. LM Studio ------------------------------------------------------ #
    def check_lmstudio(self):
        base = self.cfg.get("LLM_BASE_URL", "").rstrip("/")
        if not base:
            self.bad("LLM_BASE_URL non defini")
            return
        models_url = f"{base}/models"
        try:
            status, body = http_get(models_url, timeout=6)
        except Exception as e:
            self.bad(f"LM Studio injoignable ({models_url}) : {e}")
            return
        if status != 200:
            self.bad(f"LM Studio HTTP {status} sur {models_url}")
            return
        try:
            ids = [m["id"] for m in json.loads(body).get("data", [])]
        except Exception:
            self.bad("LM Studio : reponse /models illisible")
            return
        self.ok(f"LM Studio joignable - {len(ids)} modele(s) expose(s)")
        gen = self.cfg["GENERATION_MODEL"]
        (self.ok if gen in ids else self.bad)(
            f"Modele generation '{gen}' {'present' if gen in ids else 'ABSENT de la liste'}")

    def check_chat(self):
        if self.quick:
            self.warn("Smoke-test chat saute (--quick)")
            return
        base = self.cfg.get("LLM_BASE_URL", "").rstrip("/")
        key = self.cfg.get("LLM_API_KEY", "lm-studio")
        payload = {
            "model": self.cfg["GENERATION_MODEL"],
            "messages": [{"role": "user", "content": "Reponds uniquement: OK"}],
            "max_tokens": 64, "temperature": 0,
        }
        print(f"{INFO} Smoke-test chat (peut charger le modele en JIT, patience)...")
        t0 = time.time()
        try:
            status, body = http_post_json(f"{base}/chat/completions", payload,
                                          timeout=180, api_key=key)
            dt = time.time() - t0
            txt = json.loads(body)["choices"][0]["message"]["content"].strip()
            self.ok(f"Chat OK ({dt:.1f}s) -> '{txt[:40]}'")
        except Exception as e:
            self.bad(f"Chat KO : {e}")

    def check_embeddings(self):
        if self.quick:
            self.warn("Smoke-test embeddings saute (--quick)")
            return
        base = self.cfg.get("EMBEDDING_BASE_URL", self.cfg.get("LLM_BASE_URL", "")).rstrip("/")
        key = self.cfg.get("EMBEDDING_API_KEY", self.cfg.get("LLM_API_KEY", "lm-studio"))
        payload = {"model": self.cfg["EMBEDDING_MODEL"], "input": "hello world"}
        print(f"{INFO} Smoke-test embeddings (serveur dedie {base})...")
        t0 = time.time()
        try:
            status, body = http_post_json(f"{base}/embeddings", payload,
                                          timeout=180, api_key=key)
            dt = time.time() - t0
            dim = len(json.loads(body)["data"][0]["embedding"])
            self.ok(f"Embeddings OK ({dt:.1f}s) -> dimension = {dim}")
            print(f"       (note : memoriser cette dim ; un changement d'embedder = re-indexation)")
        except Exception as e:
            self.bad(f"Embeddings KO : {e}")

    # --- 3. Bases de donnees ---------------------------------------------- #
    def check_qdrant(self):
        host = self.cfg.get("QDRANT_HOST", "localhost")
        port = int(self.cfg.get("QDRANT_PORT", "6333"))
        url = f"http://{host}:{port}/collections"
        try:
            status, body = http_get(url, timeout=4)
            cols = json.loads(body).get("result", {}).get("collections", [])
            names = [c.get("name") for c in cols]
            self.ok(f"Qdrant joignable ({host}:{port}) - collections: {names or 'aucune'}")
            target = self.cfg.get("QDRANT_COLLECTION_NAME")
            if target and target not in names:
                self.warn(f"Collection cible '{target}' absente (a creer via l'etape 'fiches')")
        except Exception as e:
            self.bad(f"Qdrant injoignable ({host}:{port}) : {e}")

    def check_neo4j(self):
        uri = self.cfg.get("NEO4J_URI", "bolt://localhost:7687")
        # extraire host:port de bolt://host:port
        hostport = uri.split("://", 1)[-1]
        host, _, port = hostport.partition(":")
        port = int(port or 7687)
        if tcp_open(host, port, timeout=3):
            self.ok(f"Neo4j : port bolt ouvert ({host}:{port})")
        else:
            self.bad(f"Neo4j : port bolt ferme/injoignable ({host}:{port}) "
                     f"- lancer le conteneur Neo4j")

    def check_sqlite(self):
        db = ROOT / "json_db.sqlite"
        if not db.exists():
            self.warn(f"json_db.sqlite absent (cree par l'etape 'json')")
            return
        try:
            con = sqlite3.connect(str(db))
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            n = 0
            if "json_files" in tables:
                cur.execute("SELECT COUNT(*) FROM json_files")
                n = cur.fetchone()[0]
            con.close()
            self.ok(f"SQLite OK - tables: {tables}, json_files: {n} lignes")
        except Exception as e:
            self.bad(f"SQLite KO : {e}")

    # --- 4. Sources & artefacts ------------------------------------------- #
    def check_sources(self):
        code_dir = (ROOT / self.cfg.get("CODE_DIRECTORY", "./OpenFrontIO")).resolve()
        if not code_dir.exists():
            self.bad(f"Code source absent : {code_dir}")
            return
        n_ts = sum(1 for _ in code_dir.rglob("*.ts"))
        self.ok(f"Code source present : {code_dir.name} ({n_ts} fichiers .ts)")
        for name in ["project_manifest_detailed.json", "knowledge-graph.json"]:
            p = ROOT / name
            (self.ok if p.exists() else self.warn)(
                f"Artefact {name} : {'present' if p.exists() else 'absent'}")

    # --- 4 bis. Couverture langages (gate, Sprint 2) ---------------------- #
    def check_language_coverage(self):
        code_dir = (ROOT / self.cfg.get("CODE_DIRECTORY", "./OpenFrontIO")).resolve()
        if not code_dir.exists():
            self.warn("Couverture langages : code source absent")
            return
        try:
            from src.languages import build_coverage, format_matrix, unsupported_items
        except Exception as e:
            self.warn(f"Module 'languages' indisponible : {e}")
            return
        import os
        exclude = {"node_modules", "dist", "build", ".git", "coverage", ".next",
                   ".nuxt", "out", "venv", "__pycache__", ".cache", "vendor", "tmp", "temp"}
        project_root = code_dir.parent
        rels = []
        for r, dirs, files in os.walk(code_dir):
            dirs[:] = [d for d in dirs if d not in exclude]
            for f in files:
                rels.append(str((Path(r) / f).relative_to(project_root)))
        cov = build_coverage(rels)
        print(format_matrix(cov))
        items = unsupported_items(cov)
        if items:
            langs = ", ".join(sorted({e.language or "?" for e in items}))
            self.warn(f"Code non supporte present : {langs} "
                      f"(indexation bloquee sans --allow-unsupported)")
        else:
            self.ok("Couverture langages : tout le code present est supporte")

    # --- 4 ter. Backbone graphe SCIP (Lot D) ------------------------------ #
    def check_graph_backend(self):
        backend = self.cfg.get("GRAPH_BACKEND", "scip").strip().lower()
        if backend != "scip":
            self.warn(f"GRAPH_BACKEND = {backend} (rollback ts-morph actif)")
            return
        try:
            from src.graph_builder.scip_runner import image_exists, DEFAULT_IMAGE
        except Exception as e:
            self.bad(f"scip_runner indisponible : {e}")
            return
        import shutil as _sh
        if not (_sh.which("docker") or _sh.which("docker.exe")):
            self.bad("GRAPH_BACKEND=scip mais docker introuvable (Docker Desktop requis)")
            return
        if image_exists(DEFAULT_IMAGE):
            self.ok(f"Backbone SCIP : image Docker '{DEFAULT_IMAGE}' presente")
        else:
            self.bad(f"Image Docker '{DEFAULT_IMAGE}' absente -> "
                     f"docker build -f src/graph_builder/Dockerfile.indexers -t {DEFAULT_IMAGE} .")

    # --- 5. Dependances Python -------------------------------------------- #
    def check_deps(self):
        mods = {
            "langchain": "langchain", "langgraph": "langgraph",
            "langchain_openai": "langchain-openai",
            "qdrant_client": "qdrant-client", "neo4j": "neo4j",
            "chromadb": "chromadb", "sentence_transformers": "sentence-transformers",
            "tree_sitter": "tree-sitter",
            "tree_sitter_language_pack": "tree-sitter-language-pack",
        }
        import importlib.util
        missing = [pip for mod, pip in mods.items()
                   if importlib.util.find_spec(mod) is None]
        if not missing:
            self.ok("Dependances Python ML : toutes presentes")
        else:
            self.warn(f"Dependances manquantes : {missing}")
            print(f"       -> pip install {' '.join(missing)}")

    # --- run --------------------------------------------------------------- #
    def run(self):
        title("DOCTOR - diagnostic environnement RAG")
        print("\n# 1. Socle")
        self.check_python()
        self.check_config()
        print("\n# 2. Dependances Python")
        self.check_deps()
        print("\n# 3. LM Studio (generation + embeddings)")
        self.check_lmstudio()
        self.check_chat()
        self.check_embeddings()
        print("\n# 4. Bases de donnees")
        self.check_qdrant()
        self.check_neo4j()
        self.check_sqlite()
        print("\n# 5. Sources & artefacts")
        self.check_sources()
        print("\n# 6. Couverture langages (gate multi-langage)")
        self.check_language_coverage()
        print("\n# 7. Backbone graphe (SCIP/Docker)")
        self.check_graph_backend()

        title("RESUME")
        print(f"  {OK} reussis : {self.passed}")
        print(f"  {WARN} avertis : {self.warned}")
        print(f"  {FAIL} echecs  : {self.failed}")
        line()
        if self.failed == 0:
            print("  Environnement pret. Tu peux lancer les etapes d'indexation.")
        else:
            print("  Corrige les [FAIL] ci-dessus avant de lancer les etapes.")
        return 0 if self.failed == 0 else 1


# --------------------------------------------------------------------------- #
#  ETAPES ETL / QUERY (imports paresseux)                                      #
# --------------------------------------------------------------------------- #
def _setup_logging():
    """Rend visibles les logs des modules src/ (sinon muets)."""
    import logging
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )


def _stage(label: str, fn):
    """Execute une etape en capturant proprement les erreurs d'import/exec."""
    _setup_logging()
    title(label)
    try:
        fn()
        print(f"\n{OK} Etape terminee.")
        return 0
    except ImportError as e:
        print(f"\n{FAIL} Dependance manquante : {e}")
        print("       -> active le venv et installe requirements.txt, puis relance.")
        return 1
    except Exception as e:
        import traceback
        print(f"\n{FAIL} Echec de l'etape : {e}\n")
        traceback.print_exc()
        return 1


def stage_audit(allow_unsupported: bool = False):
    from src.audit_and_filter import run_audit_and_save_manifest
    run_audit_and_save_manifest(min_score=5, verbose=True,
                                allow_unsupported=allow_unsupported, interactive=False)


def stage_graph():
    from src.graph_indexer import build_knowledge_graph
    build_knowledge_graph()


def stage_fiches(resume: bool = False):
    from src.metadata_enricher import MetadataEnricher
    MetadataEnricher().run_enrichment_pipeline(resume=resume)


def stage_chunks():
    from src.vectordb_indexer import index_codebase
    index_codebase()


def stage_json():
    from src.json_indexer import index_json_files
    index_json_files()


def stage_query():
    # Agent ReAct borne (a brancher au prochain jalon : src/agent_react.py)
    try:
        from src.agent_react import run_interactive
    except ImportError:
        print(f"{WARN} L'agent (src/agent_react.py) n'est pas encore construit.")
        print("       C'est le livrable du prochain jalon.")
        return
    run_interactive()


# --------------------------------------------------------------------------- #
#  MENU interactif                                                             #
# --------------------------------------------------------------------------- #
MENU = """
============================================================
  CODE EXPERT RAG - Orchestrateur (LM Studio)
============================================================
  0. Doctor (diagnostic complet)
  ---------------------------------------------------------
  1. Audit            (manifest filtre)
  2. Graph  Neo4j     (knowledge graph)
  3. Fiches Qdrant    (fiches conceptuelles)
  4. Chunks           (code)
  5. JSON   SQLite    (config)
  ---------------------------------------------------------
  6. Query (agent)
  q. Quitter
============================================================"""


def menu_loop(cfg):
    actions = {
        "1": ("ETAPE 1 - Audit", stage_audit),
        "2": ("ETAPE 2 - Graph Neo4j", stage_graph),
        "3": ("ETAPE 3 - Fiches Qdrant", stage_fiches),
        "4": ("ETAPE 4 - Chunks", stage_chunks),
        "5": ("ETAPE 5 - JSON SQLite", stage_json),
        "6": ("ETAPE 6 - Query", stage_query),
    }
    while True:
        print(MENU)
        choice = input("\n> ").strip().lower()
        if choice in ("q", "0q", "quit", "exit"):
            print("Au revoir.")
            return 0
        if choice == "0":
            Doctor(cfg).run()
        elif choice in actions:
            label, fn = actions[choice]
            _stage(label, fn)
        else:
            print(f"{WARN} Choix invalide : '{choice}'")


# --------------------------------------------------------------------------- #
#  Entrypoint                                                                  #
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(description="Orchestrateur RAG Code Expert")
    sub = parser.add_subparsers(dest="cmd")
    d = sub.add_parser("doctor", help="Diagnostic environnement")
    d.add_argument("--quick", action="store_true", help="Saute les smoke-tests LLM")
    a = sub.add_parser("audit", help="Etape 1 : manifest")
    a.add_argument("--allow-unsupported", action="store_true",
                   help="Indexer TS/JS meme si du code non supporte est present (override du gate)")
    sub.add_parser("graph", help="Etape 2 : Neo4j")
    fp = sub.add_parser("fiches", help="Etape 3 : Qdrant")
    fp.add_argument("--resume", action="store_true",
                    help="Reprise : conserve la collection, saute les fichiers deja indexes")
    sub.add_parser("chunks", help="Etape 4 : chunks")
    sub.add_parser("json", help="Etape 5 : SQLite")
    sub.add_parser("query", help="Etape 6 : agent")
    sub.add_parser("menu", help="Menu interactif")
    args = parser.parse_args(argv)

    cfg = load_config()
    if cfg is None:
        return 1

    cmd = args.cmd or "menu"
    if cmd == "doctor":
        return Doctor(cfg, quick=getattr(args, "quick", False)).run()
    if cmd == "menu":
        return menu_loop(cfg)
    if cmd == "audit":
        allow = getattr(args, "allow_unsupported", False)
        return _stage("ETAPE 1 - Audit", lambda: stage_audit(allow_unsupported=allow))
    if cmd == "fiches":
        resume = getattr(args, "resume", False)
        return _stage("ETAPE 3 - Fiches Qdrant", lambda: stage_fiches(resume=resume))
    dispatch = {
        "audit": ("ETAPE 1 - Audit", stage_audit),
        "graph": ("ETAPE 2 - Graph Neo4j", stage_graph),
        "fiches": ("ETAPE 3 - Fiches Qdrant", stage_fiches),
        "chunks": ("ETAPE 4 - Chunks", stage_chunks),
        "json": ("ETAPE 5 - JSON SQLite", stage_json),
        "query": ("ETAPE 6 - Query", stage_query),
    }
    label, fn = dispatch[cmd]
    return _stage(label, fn)


if __name__ == "__main__":
    sys.exit(main())
