# src/graph_builder/scip_runner.py
#
# Sprint 2 - Phase 0 (Lot D) : execute un indexeur SCIP dans Docker/Linux.
#
# Runtime Linux unique (image code-expert-indexers) pour produire un index.scip,
# quel que soit le langage (Phase 0 : scip-typescript). Sortie ECRITE HORS du
# projet (dossier dedie) pour ne pas polluer le repo analyse.
#
# Contraintes connues (cf. handoff) :
#   - docker introuvable depuis subprocess Windows sans resolution -> shutil.which.
#   - Git Bash transforme les chemins -v/-w -> MSYS_NO_PATHCONV (mais ici on lance
#     docker.exe directement via subprocess Python : pas de shell MSYS, donc OK ;
#     on positionne quand meme la variable d'env par securite).
#   - scip-typescript : tsconfig.json present -> utilise tel quel ; absent -> --infer-tsconfig.

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_IMAGE = "code-expert-indexers:latest"
# Dossier de sortie des index SCIP (hors projet analyse).
SCIP_OUT_DIRNAME = ".scip_out"


class ScipRunError(RuntimeError):
    pass


def _docker_exe() -> str:
    exe = shutil.which("docker") or shutil.which("docker.exe")
    if not exe:
        raise ScipRunError("docker introuvable dans le PATH (Docker Desktop requis).")
    return exe


def image_exists(image: str = DEFAULT_IMAGE) -> bool:
    """True si l'image Docker des indexeurs est presente localement."""
    try:
        exe = _docker_exe()
    except ScipRunError:
        return False
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    r = subprocess.run([exe, "image", "inspect", image],
                       capture_output=True, text=True, env=env)
    return r.returncode == 0


def run_scip_typescript(
    project_dir: Path,
    out_dir: Path,
    image: str = DEFAULT_IMAGE,
    output_name: str = "index.scip",
) -> Path:
    """Lance scip-typescript dans Docker sur `project_dir`. Retourne le chemin de l'index.

    project_dir : racine du projet TS/JS (contient idealement tsconfig.json).
    out_dir     : dossier hote ou ecrire l'index (cree si besoin).
    """
    exe = _docker_exe()
    project_dir = Path(project_dir).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not project_dir.exists():
        raise ScipRunError(f"Projet introuvable : {project_dir}")
    if not image_exists(image):
        raise ScipRunError(
            f"Image Docker '{image}' absente. Construire avec :\n"
            f"  docker build -f src/graph_builder/Dockerfile.indexers -t {image} .")

    has_tsconfig = (project_dir / "tsconfig.json").exists()

    # Chemins Docker en POSIX (Windows accepte E:/... pour -v).
    proj_mount = str(project_dir).replace("\\", "/")
    out_mount = str(out_dir).replace("\\", "/")

    cmd = [
        exe, "run", "--rm",
        "-v", f"{proj_mount}:/work",
        "-v", f"{out_mount}:/out",
        "-w", "/work",
        image,
        "scip-typescript", "index",
        "--output", f"/out/{output_name}",
        "--no-progress-bar",
    ]
    if not has_tsconfig:
        cmd.append("--infer-tsconfig")
        log.warning("Pas de tsconfig.json -> --infer-tsconfig (resolution de types degradee).")

    log.info(f"SCIP (Docker) : indexation TS de {project_dir.name} ...")
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        log.error("scip-typescript (Docker) a echoue.")
        log.error("STDOUT:\n" + (r.stdout or ""))
        log.error("STDERR:\n" + (r.stderr or ""))
        raise ScipRunError(f"scip-typescript code {r.returncode}")

    index_path = out_dir / output_name
    if not index_path.exists():
        raise ScipRunError(f"Index SCIP non produit : {index_path}")
    log.info(f"SCIP index produit : {index_path} ({index_path.stat().st_size} octets)")
    return index_path


def run_scip_python(
    project_dir: Path,
    out_dir: Path,
    project_name: str,
    image: str = DEFAULT_IMAGE,
    output_name: str = "index.scip",
) -> Path:
    """Lance scip-python (Pyright) dans Docker sur `project_dir`. Retourne l'index.

    Cree un pyrightconfig.json par defaut ({"include":["."]}) si absent (requis
    par scip-python). project_name = nom du projet SCIP.
    """
    exe = _docker_exe()
    project_dir = Path(project_dir).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not project_dir.exists():
        raise ScipRunError(f"Projet introuvable : {project_dir}")
    if not image_exists(image):
        raise ScipRunError(
            f"Image Docker '{image}' absente. Construire avec :\n"
            f"  docker build -f src/graph_builder/Dockerfile.indexers -t {image} .")

    cfg = project_dir / "pyrightconfig.json"
    if not cfg.exists():
        cfg.write_text('{"include": ["."]}', encoding="utf-8")
        log.warning(f"pyrightconfig.json absent -> cree par defaut : {cfg}")

    proj_mount = str(project_dir).replace("\\", "/")
    out_mount = str(out_dir).replace("\\", "/")
    cmd = [
        exe, "run", "--rm",
        "-v", f"{proj_mount}:/work",
        "-v", f"{out_mount}:/out",
        "-w", "/work",
        image,
        "scip-python", "index",
        "--project-name", project_name,
        "--project-version", "HEAD",
        "--output", f"/out/{output_name}",
    ]
    log.info(f"SCIP (Docker) : indexation Python de {project_dir.name} ...")
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        log.error("scip-python (Docker) a echoue.")
        log.error("STDOUT:\n" + (r.stdout or ""))
        log.error("STDERR:\n" + (r.stderr or ""))
        raise ScipRunError(f"scip-python code {r.returncode}")

    index_path = out_dir / output_name
    if not index_path.exists():
        raise ScipRunError(f"Index SCIP non produit : {index_path}")
    log.info(f"SCIP index produit : {index_path} ({index_path.stat().st_size} octets)")
    return index_path


# Dispatch par langage (nom du LanguageProvider) -> runner.
def run_scip_for_language(language: str, project_dir: Path, out_dir: Path,
                          project_name: str, image: str = DEFAULT_IMAGE) -> Path:
    if language in ("typescript", "javascript"):
        return run_scip_typescript(project_dir, out_dir, image=image)
    if language == "python":
        return run_scip_python(project_dir, out_dir, project_name, image=image)
    raise ScipRunError(f"Pas d'indexeur SCIP pour le langage '{language}'.")


# --- Debug standalone ------------------------------------------------------- #
if __name__ == "__main__":
    import configparser
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    root = Path(__file__).resolve().parent.parent.parent
    cfg = configparser.ConfigParser(); cfg.read(root / "config.ini")
    code_dir = (root / cfg["DEFAULT"]["CODE_DIRECTORY"]).resolve()
    out = root / SCIP_OUT_DIRNAME
    p = run_scip_typescript(code_dir, out)
    print(f"OK -> {p}")
