#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — Interface guidee du RAG Code Expert (analyse de N'IMPORTE QUEL projet TS/JS).

Lance TOUT depuis un seul endroit :
  - verifie/demarre les services (embeddings, Neo4j, Qdrant) + check LM Studio
  - gere les SOURCES du projet (cloner un depot git / pointer un dossier / git pull)
  - indexe les 4 sources, ouvre l'agent
  - "PROCEDURE COMPLETE" qui deroule tout

    python main.py

Les etapes d'indexation/agent sont lancees via sous-processus `rag.py` afin de
relire la config a jour (utile apres un changement de projet source).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from rag import ROOT, load_config, OK, FAIL, WARN, INFO, line, title

PY = sys.executable  # le meme interpreteur que celui qui lance main.py
CFG = load_config()


# --------------------------------------------------------------------------- #
#  Sondes / services                                                          #
# --------------------------------------------------------------------------- #
def _http_ok(url, timeout=4.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _tcp_ok(host, port, timeout=3.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def svc_lmstudio():
    return _http_ok(CFG["LLM_BASE_URL"].rstrip("/") + "/models")


def svc_lmstudio_model():
    try:
        with urllib.request.urlopen(CFG["LLM_BASE_URL"].rstrip("/") + "/models", timeout=5) as r:
            ids = [m["id"] for m in json.loads(r.read())["data"]]
        return CFG["GENERATION_MODEL"] in ids
    except Exception:
        return False


def svc_embed():
    return _http_ok(CFG.get("EMBEDDING_BASE_URL", "http://localhost:8090/v1").rstrip("/") + "/models")


def svc_qdrant():
    return _http_ok(f"http://{CFG['QDRANT_HOST']}:{CFG['QDRANT_PORT']}/collections")


def svc_neo4j():
    h, _, p = CFG["NEO4J_URI"].split("://", 1)[-1].partition(":")
    return _tcp_ok(h, int(p or 7687))


def status_line():
    m = lambda ok: "ON " if ok else "off"
    return (f"LMStudio[{m(svc_lmstudio())}] Embed[{m(svc_embed())}] "
            f"Neo4j[{m(svc_neo4j())}] Qdrant[{m(svc_qdrant())}]")


def _wait(check, secs, label):
    print(f"{INFO} Attente {label} (max {secs}s)...", end="", flush=True)
    for _ in range(secs):
        if check():
            print(f" {OK}")
            return True
        time.sleep(1)
    print(f" {FAIL} (timeout)")
    return False


def start_embed_server():
    if svc_embed():
        print(f"{OK} Embeddings deja actif (:8090).")
        return True
    exe = ROOT / "tools" / "llamacpp" / "llama-server.exe"
    gguf = CFG.get("EMBEDDING_GGUF", "")
    if not exe.exists():
        print(f"{FAIL} llama-server introuvable : {exe}"); return False
    if not gguf or not Path(gguf).exists():
        print(f"{FAIL} GGUF introuvable : {gguf}"); return False
    print(f"{INFO} Demarrage serveur d'embeddings (nouvelle fenetre)...")
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    subprocess.Popen([str(exe), "-m", gguf, "--embeddings", "--pooling", "last",
                      "-ngl", "99", "--host", "0.0.0.0", "--port", "8090",
                      "-c", "4096", "-b", "4096", "-ub", "2048"], creationflags=flags)
    return _wait(svc_embed, 90, "embeddings")


def start_neo4j():
    if svc_neo4j():
        print(f"{OK} Neo4j deja actif."); return True
    print(f"{INFO} docker compose up -d neo4j ...")
    try:
        subprocess.run(["docker", "compose", "up", "-d", "neo4j"], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=120)
    except Exception as e:
        print(f"{FAIL} docker : {e}"); return False
    return _wait(svc_neo4j, 90, "Neo4j")


def start_qdrant():
    if svc_qdrant():
        print(f"{OK} Qdrant deja actif."); return True
    print(f"{INFO} docker start qdrant ...")
    try:
        subprocess.run(["docker", "start", "qdrant"], capture_output=True, text=True, timeout=60)
    except Exception as e:
        print(f"{FAIL} docker : {e}"); return False
    return _wait(svc_qdrant, 40, "Qdrant")


def ensure_services(need_gen=True):
    title("VERIFICATION DES SERVICES")
    ok = True
    start_embed_server(); ok &= svc_embed()
    start_neo4j(); ok &= svc_neo4j()
    start_qdrant(); ok &= svc_qdrant()
    if need_gen:
        if svc_lmstudio_model():
            print(f"{OK} LM Studio : '{CFG['GENERATION_MODEL']}' disponible.")
        else:
            ok = False
            print(f"{FAIL} LM Studio : '{CFG['GENERATION_MODEL']}' absent/non charge.")
            print(f"       -> lms load {CFG['GENERATION_MODEL']} -c 12288 --parallel 1 --gpu max -y")
    print(f"\n  => {'TOUT EST PRET.' if ok else 'Des services manquent.'}")
    return ok


# --------------------------------------------------------------------------- #
#  Etapes via sous-processus rag.py (config relue a jour)                     #
# --------------------------------------------------------------------------- #
def run_stage(label, cmd, extra=None):
    title(label)
    args = [PY, "rag.py", cmd] + (extra or [])
    rc = subprocess.run(args, cwd=str(ROOT)).returncode
    print(f"\n{OK if rc == 0 else FAIL} {label} (code {rc})")
    return rc


# --------------------------------------------------------------------------- #
#  Gate multi-langage (Sprint 2, Phase 0 - Lot C)                             #
# --------------------------------------------------------------------------- #
def gate_scan_and_decide():
    """Affiche la matrice de couverture ; si du code non supporte est present,
    DEMANDE a l'humain. Retourne (proceed: bool, allow_unsupported: bool)."""
    try:
        from src.languages import (build_coverage, format_matrix, format_alert,
                                    unsupported_items)
    except Exception as e:
        print(f"{FAIL} Module 'languages' indisponible : {e}")
        return (False, False)
    code_dir = (ROOT / CFG["CODE_DIRECTORY"]).resolve()
    if not code_dir.exists():
        print(f"{FAIL} Code source absent : {code_dir}")
        return (False, False)
    exclude = {"node_modules", "dist", "build", ".git", "coverage", ".next",
               ".nuxt", "out", "venv", "__pycache__", ".cache", "vendor", "tmp", "temp"}
    project_root = code_dir.parent
    rels = []
    for r, dirs, files in os.walk(code_dir):
        dirs[:] = [d for d in dirs if d not in exclude]
        for f in files:
            rels.append(str((Path(r) / f).relative_to(project_root)))
    cov = build_coverage(rels)
    title("COUVERTURE LANGAGES (gate)")
    print(format_matrix(cov))
    items = unsupported_items(cov)
    if not items:
        print(f"\n{OK} Tout le code present est supporte.")
        return (True, False)
    print(format_alert(cov))
    print(f"\n{WARN} Ce code NE SERA PAS indexe (seuls TS/JS + texte/config le seront).")
    ans = input("Continuer quand meme ? [o/N] ").strip().lower()
    if ans in ("o", "oui", "y", "yes"):
        return (True, True)
    print(f"{FAIL} Indexation annulee (gate).")
    return (False, False)


INDEX_ORDER = [("Audit", "audit"), ("Graph Neo4j", "graph"), ("Fiches Qdrant", "fiches"),
               ("Chunks Chroma", "chunks"), ("JSON SQLite", "json")]


def index_all():
    title("INDEXATION COMPLETE")
    print(f"Projet : {CFG['CODE_DIRECTORY']}")
    print("Ordre : audit -> graph -> fiches -> chunks -> json")
    print("(fiches = etape longue : generation + embeddings sur tous les fichiers)\n")
    if input("Lancer l'indexation complete ? [o/N] ").strip().lower() not in ("o", "oui", "y"):
        print("Annule."); return
    # Gate multi-langage : matrice + decision humaine si code non supporte.
    proceed, allow = gate_scan_and_decide()
    if not proceed:
        print(f"\n{FAIL} Indexation stoppee par le gate."); return
    for label, cmd in INDEX_ORDER:
        extra = ["--allow-unsupported"] if (cmd == "audit" and allow) else None
        if run_stage(label, cmd, extra) != 0:
            print(f"\n{FAIL} Arret : '{label}' a echoue."); return
    print(f"\n{OK} Indexation complete terminee.")


# --------------------------------------------------------------------------- #
#  Gestion des SOURCES (analyser n'importe quel projet)                       #
# --------------------------------------------------------------------------- #
_IGNORE = shutil.ignore_patterns("node_modules", ".git", "dist", "build", "out",
                                 ".next", ".nuxt", "coverage", "__pycache__", ".cache")


def _set_code_directory(value):
    """Ecrit CODE_DIRECTORY dans config.ini en preservant les commentaires."""
    global CFG
    p = ROOT / "config.ini"
    txt = p.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^CODE_DIRECTORY\s*=.*$", f"CODE_DIRECTORY = {value}", txt)
    if n == 0:
        new = txt.replace("[DEFAULT]", f"[DEFAULT]\nCODE_DIRECTORY = {value}", 1)
    p.write_text(new, encoding="utf-8")
    CFG = load_config()
    print(f"{OK} CODE_DIRECTORY = {value}")


def _wipe_old_artifacts():
    """Supprime manifest/graphe de l'ancien projet (les BDD sont reecrites a l'index)."""
    for f in ("project_manifest.txt", "project_manifest_detailed.json", "knowledge-graph.json"):
        try:
            (ROOT / f).unlink()
        except FileNotFoundError:
            pass


def _is_git_url(s):
    return bool(re.match(r"^(https?://|git@|ssh://)", s)) or s.endswith(".git")


def source_clone(url):
    name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]) or "projet"
    dest = ROOT / name
    if dest.exists():
        print(f"{WARN} {dest} existe deja.")
        if input("Ecraser (git pull si repo, sinon supprimer+clone) ? [o/N] ").strip().lower() in ("o", "y"):
            if (dest / ".git").exists():
                subprocess.run(["git", "-C", str(dest), "pull"]); _post_source(name); return
            shutil.rmtree(dest, ignore_errors=True)
        else:
            return
    print(f"{INFO} git clone {url} -> {dest} ...")
    rc = subprocess.run(["git", "clone", "--depth", "1", url, str(dest)]).returncode
    if rc != 0:
        print(f"{FAIL} clone echoue."); return
    _post_source(name)


def source_local(path_str):
    src = Path(path_str.strip().strip('"'))
    if not src.exists() or not src.is_dir():
        print(f"{FAIL} Dossier introuvable : {src}"); return
    try:
        rel = src.resolve().relative_to(ROOT.resolve())
        _set_code_directory("./" + str(rel).replace("\\", "/"))  # deja sous le root
        _wipe_old_artifacts()
        _offer_reindex(); return
    except ValueError:
        pass  # hors du root -> on copie
    dest = ROOT / src.name
    print(f"{INFO} Le projet est hors de {ROOT.name}/ -> copie vers {dest} (exclut node_modules/.git)...")
    if dest.exists():
        print(f"{WARN} {dest} existe deja, copie annulee. Supprime-le ou choisis 'git pull'."); return
    shutil.copytree(src, dest, ignore=_IGNORE)
    _post_source(src.name)


def source_pull():
    cur = (ROOT / CFG["CODE_DIRECTORY"]).resolve()
    if not (cur / ".git").exists():
        print(f"{WARN} {cur} n'est pas un depot git (git pull impossible)."); return
    print(f"{INFO} git pull dans {cur} ...")
    subprocess.run(["git", "-C", str(cur), "pull"])
    _offer_reindex()


def _post_source(name):
    _set_code_directory(f"./{name}")
    _wipe_old_artifacts()
    _offer_reindex()


def _offer_reindex():
    print(f"\n{INFO} Source mise a jour. Les anciens index seront remplaces a la re-indexation.")
    if input("Re-indexer maintenant (indexation complete) ? [o/N] ").strip().lower() in ("o", "oui", "y"):
        if ensure_services(need_gen=True):
            index_all()
        else:
            print(f"{WARN} Demarre les services manquants puis relance l'indexation (A).")


def source_menu():
    while True:
        title("SOURCES DU PROJET")
        print(f"Projet courant : CODE_DIRECTORY = {CFG['CODE_DIRECTORY']}")
        print("  (note : graphe + chunks sont optimises pour TS/JS)\n")
        print("  1. Cloner un depot git (URL)")
        print("  2. Pointer un dossier local")
        print("  3. Mettre a jour le projet courant (git pull)")
        print("  4. Retour")
        c = input("> ").strip()
        if c == "1":
            url = input("URL du depot git : ").strip()
            if url:
                source_clone(url)
        elif c == "2":
            p = input("Chemin du dossier projet : ").strip()
            if p:
                source_local(p)
        elif c == "3":
            source_pull()
        elif c in ("4", "", "q"):
            return
        else:
            print(f"{WARN} Choix invalide.")


# --------------------------------------------------------------------------- #
#  Procedure complete                                                         #
# --------------------------------------------------------------------------- #
def procedure_complete():
    title("PROCEDURE COMPLETE GUIDEE")
    print("Verif services -> doctor -> indexation -> agent\n")
    if input("Demarrer ? [o/N] ").strip().lower() not in ("o", "oui", "y"):
        return
    if not ensure_services(need_gen=True):
        print(f"\n{WARN} Corrige les services manquants d'abord."); return
    run_stage("Diagnostic", "doctor")
    index_all()
    if input("\nOuvrir l'agent ? [o/N] ").strip().lower() in ("o", "oui", "y"):
        run_stage("Agent", "query")


# --------------------------------------------------------------------------- #
#  Menu                                                                        #
# --------------------------------------------------------------------------- #
def menu():
    while True:
        print("\n" + "=" * 64)
        print("  CODE EXPERT RAG   |   Interface principale")
        print("=" * 64)
        print(f"  Projet  : {CFG['CODE_DIRECTORY']}")
        print(f"  Services: {status_line()}")
        line()
        print("  P. >> PROCEDURE COMPLETE (verif -> index -> agent) <<")
        line()
        print("  0. Doctor (diagnostic)        S. Demarrer les services")
        print("  M. Sources du projet (cloner / dossier / git pull)")
        print("  -- Indexation --")
        print("  1.Audit  2.Graph  3.Fiches  4.Chunks  5.JSON   A.TOUT indexer")
        print("  -- Utilisation --")
        print("  I. Interroger l'agent          q. Quitter")
        print("=" * 64)
        c = input("> ").strip().lower()
        if c in ("q", "quit", "exit"):
            print("Au revoir."); return
        elif c == "p": procedure_complete()
        elif c == "0": run_stage("Doctor", "doctor")
        elif c == "s": ensure_services(need_gen=True)
        elif c == "m": source_menu()
        elif c == "1": run_stage("Audit", "audit")
        elif c == "2": run_stage("Graph Neo4j", "graph")
        elif c == "3": run_stage("Fiches Qdrant", "fiches")
        elif c == "4": run_stage("Chunks Chroma", "chunks")
        elif c == "5": run_stage("JSON SQLite", "json")
        elif c == "a": index_all()
        elif c == "i": run_stage("Agent", "query")
        else: print(f"{WARN} Choix invalide : '{c}'")


if __name__ == "__main__":
    if CFG is None:
        sys.exit(1)
    try:
        menu()
    except KeyboardInterrupt:
        print("\nInterrompu.")
