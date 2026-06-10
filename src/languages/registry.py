# src/languages/registry.py
#
# Sprint 2 - Phase 0 (Lot A) : registre central des langages + classification fichier.
#
# - LANGUAGE_REGISTRY : { name -> LanguageProvider }.
# - provider_for_ext / provider_for_path : resolution extension -> provider.
# - classify_file : CODE / TEXT / CONFIG / UNKNOWN (separe le couplage-langage des
#   sources agnostiques deja gerees ailleurs).
#
# Stdlib pure (importable dans doctor). Le gate (Lot C) s'appuiera sur ce module.

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .base import FileCategory, LanguageProvider
from .typescript import PROVIDER as _TS
from .javascript import PROVIDER as _JS

# --------------------------------------------------------------------------- #
#  Categories agnostiques (non couplees a un LanguageProvider)                 #
# --------------------------------------------------------------------------- #
# TEXT  : traites par RecursiveCharacterTextSplitter (chunks) + fiches LLM.
# CONFIG: traites par json_indexer (SQLite).
_TEXT_EXTENSIONS = frozenset({".md", ".mdx", ".yml", ".yaml"})
_CONFIG_EXTENSIONS = frozenset({".json"})

# --------------------------------------------------------------------------- #
#  Registre                                                                    #
# --------------------------------------------------------------------------- #
_PROVIDERS: tuple[LanguageProvider, ...] = (_TS, _JS)

LANGUAGE_REGISTRY: dict[str, LanguageProvider] = {p.name: p for p in _PROVIDERS}

# Index extension -> provider, avec garde anti-collision (un .ext = un seul langage).
_EXT_INDEX: dict[str, LanguageProvider] = {}
for _p in _PROVIDERS:
    for _ext in _p.extensions:
        if _ext in _EXT_INDEX:
            raise RuntimeError(
                f"Collision d'extension '{_ext}' entre '{_EXT_INDEX[_ext].name}' "
                f"et '{_p.name}' dans le registre."
            )
        if _ext in _TEXT_EXTENSIONS or _ext in _CONFIG_EXTENSIONS:
            raise RuntimeError(
                f"Extension '{_ext}' du provider '{_p.name}' entre en conflit avec "
                f"une categorie agnostique (TEXT/CONFIG)."
            )
        _EXT_INDEX[_ext] = _p


# --------------------------------------------------------------------------- #
#  API publique                                                               #
# --------------------------------------------------------------------------- #
def _ext_of(path: Union[str, Path]) -> str:
    """Extension en minuscules d'un chemin (gere les slashs ET antislashs)."""
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:].lower() if dot > 0 else ""


def provider_for_ext(ext: str) -> Optional[LanguageProvider]:
    """Provider couvrant cette extension (ex '.ts'), ou None."""
    if ext and not ext.startswith("."):
        ext = "." + ext
    return _EXT_INDEX.get(ext.lower())


def provider_for_path(path: Union[str, Path]) -> Optional[LanguageProvider]:
    """Provider couvrant ce fichier d'apres son extension, ou None."""
    return _EXT_INDEX.get(_ext_of(path))


def classify_file(path: Union[str, Path]) -> FileCategory:
    """Categorie du fichier vis-a-vis du pipeline (CODE/TEXT/CONFIG/UNKNOWN)."""
    ext = _ext_of(path)
    if ext in _EXT_INDEX:
        return FileCategory.CODE
    if ext in _TEXT_EXTENSIONS:
        return FileCategory.TEXT
    if ext in _CONFIG_EXTENSIONS:
        return FileCategory.CONFIG
    return FileCategory.UNKNOWN


def all_extensions() -> frozenset[str]:
    """Toutes les extensions CODE connues du registre (tous statuts confondus)."""
    return frozenset(_EXT_INDEX.keys())


def validated_languages() -> tuple[str, ...]:
    """Noms des langages au statut VALIDATED."""
    return tuple(p.name for p in _PROVIDERS if p.is_validated)


# --------------------------------------------------------------------------- #
#  Gate : support par fichier (Sprint 2, Phase 0 - Lot C)                      #
# --------------------------------------------------------------------------- #
# Langages de programmation GENERALISTES reconnus mais (pour l'instant) sans
# provider VALIDATED. Sert au gate a distinguer "code non supporte" (= a
# signaler, potentiellement bloquant) d'un simple asset/markup non pertinent.
# glsl/css/html/sql/sass... = volontairement HORS de cette liste -> IGNORED
# (listes dans la matrice, jamais bloquants : hors perimetre analyse de programme).
_RECOGNIZED_CODE: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".c": "c", ".h": "c",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "bash", ".bash": "bash",
}

# Statuts retournes par language_support (chaines stables, utilisees par le gate).
ST_VALIDATED = "VALIDATED"        # provider VALIDATED -> indexe
ST_EXPERIMENTAL = "EXPERIMENTAL"  # provider EXPERIMENTAL -> PAS indexe (gate)
ST_UNSUPPORTED = "UNSUPPORTED"    # code reconnu sans provider -> gate
ST_TEXT = "TEXT"                  # md/yml -> agnostique (indexe : fiches/chunks texte)
ST_CONFIG = "CONFIG"             # json -> agnostique (json_indexer/SQLite)
ST_IGNORED = "IGNORED"           # asset/markup/inconnu -> non pertinent (non bloquant)


def language_support(path: Union[str, Path]) -> tuple[str, Optional[str]]:
    """Retourne (status, language) pour le gate.

    - provider present  -> (status du provider, provider.name)   [VALIDATED/EXPERIMENTAL]
    - extension TEXT    -> (ST_TEXT, None)
    - extension CONFIG  -> (ST_CONFIG, None)
    - code reconnu sans provider -> (ST_UNSUPPORTED, <langage>)
    - reste             -> (ST_IGNORED, None)
    """
    ext = _ext_of(path)
    p = _EXT_INDEX.get(ext)
    if p is not None:
        return (p.status, p.name)
    if ext in _TEXT_EXTENSIONS:
        return (ST_TEXT, None)
    if ext in _CONFIG_EXTENSIONS:
        return (ST_CONFIG, None)
    if ext in _RECOGNIZED_CODE:
        return (ST_UNSUPPORTED, _RECOGNIZED_CODE[ext])
    return (ST_IGNORED, None)
