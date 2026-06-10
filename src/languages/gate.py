# src/languages/gate.py
#
# Sprint 2 - Phase 0 (Lot C) : le GATE multi-langage.
#
# Principe (decide avec le proprietaire) :
#   - On ALERTE TOUJOURS (jamais d'ignore silencieux) : matrice de couverture +
#     liste explicite du code reconnu mais NON supporte.
#   - On STOP seulement si l'HUMAIN le decide : l'importance d'un code non
#     supporte est jugee par l'humain, pas par une heuristique (le scoreur
#     d'audit est biaise TS/JS). En interactif -> on demande. En mode script
#     non-interactif -> STOP par defaut (sur), contournable par allow_unsupported.
#
# Ce qui est INDEXE = uniquement les langages VALIDATED + agnostique (TEXT/CONFIG).
# EXPERIMENTAL et UNSUPPORTED ne sont JAMAIS indexes (pas de fallback degrade).
#
# Stdlib pure (importable dans rag.py doctor). Opere sur des chemins relatifs.

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .registry import (
    ST_CONFIG,
    ST_EXPERIMENTAL,
    ST_IGNORED,
    ST_TEXT,
    ST_UNSUPPORTED,
    ST_VALIDATED,
    language_support,
)

# Ordre d'affichage des statuts dans la matrice.
_STATUS_ORDER = [ST_VALIDATED, ST_EXPERIMENTAL, ST_UNSUPPORTED, ST_TEXT, ST_CONFIG, ST_IGNORED]

# Statuts de code qui ne sont PAS indexables -> declenchent l'alerte / le gate.
_BLOCKING_STATUSES = (ST_UNSUPPORTED, ST_EXPERIMENTAL)

_MAX_EXAMPLES = 5


@dataclass
class CoverageEntry:
    status: str
    language: Optional[str]
    count: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return (self.status, self.language or "")


@dataclass
class Coverage:
    entries: list[CoverageEntry]
    total_files: int

    def blocking(self) -> list[CoverageEntry]:
        """Entrees de code non indexable (UNSUPPORTED / EXPERIMENTAL)."""
        return [e for e in self.entries if e.status in _BLOCKING_STATUSES]

    def indexed_code_languages(self) -> list[str]:
        return sorted({e.language for e in self.entries
                       if e.status == ST_VALIDATED and e.language})


def build_coverage(rel_paths: Iterable[str]) -> Coverage:
    """Construit la couverture (comptes + exemples) sur un ensemble de chemins."""
    acc: dict[tuple[str, str], CoverageEntry] = {}
    total = 0
    for rel in rel_paths:
        total += 1
        status, lang = language_support(rel)
        k = (status, lang or "")
        e = acc.get(k)
        if e is None:
            e = CoverageEntry(status=status, language=lang)
            acc[k] = e
        e.count += 1
        if len(e.examples) < _MAX_EXAMPLES:
            e.examples.append(str(rel).replace("\\", "/"))

    def sort_key(e: CoverageEntry):
        si = _STATUS_ORDER.index(e.status) if e.status in _STATUS_ORDER else len(_STATUS_ORDER)
        return (si, -(e.count), e.language or "")

    return Coverage(entries=sorted(acc.values(), key=sort_key), total_files=total)


def unsupported_items(coverage: Coverage) -> list[CoverageEntry]:
    """Code reconnu non indexable (a signaler / potentiellement bloquant)."""
    return coverage.blocking()


def format_matrix(coverage: Coverage) -> str:
    """Rend la matrice de couverture (texte ASCII)."""
    lines = []
    lines.append("MATRICE DE COUVERTURE (par statut / langage)")
    lines.append(f"  {'statut':<13} {'langage':<12} {'fichiers':>8}   exemple")
    lines.append("  " + "-" * 60)
    for e in coverage.entries:
        ex = e.examples[0] if e.examples else ""
        lang = e.language or "-"
        lines.append(f"  {e.status:<13} {lang:<12} {e.count:>8}   {ex}")
    lines.append("  " + "-" * 60)
    lines.append(f"  total fichiers scannes : {coverage.total_files}")
    idx = coverage.indexed_code_languages()
    lines.append(f"  langages indexes (VALIDATED) : {', '.join(idx) if idx else 'aucun'}")
    return "\n".join(lines)


def format_alert(coverage: Coverage) -> str:
    """Rend l'alerte detaillee pour le code non supporte (avec chemins)."""
    items = unsupported_items(coverage)
    if not items:
        return ""
    lines = ["", "!!! CODE NON SUPPORTE DETECTE (ne sera PAS indexe) !!!"]
    for e in items:
        label = e.language or "?"
        tag = "experimental" if e.status == ST_EXPERIMENTAL else "non supporte"
        lines.append(f"  - {label} ({tag}) : {e.count} fichier(s)")
        for ex in e.examples:
            lines.append(f"      {ex}")
        if e.count > len(e.examples):
            lines.append(f"      ... (+{e.count - len(e.examples)} autres)")
    return "\n".join(lines)


@dataclass
class GateDecision:
    proceed: bool
    reason: str


def decide(
    coverage: Coverage,
    *,
    interactive: bool = False,
    allow_unsupported: bool = False,
    prompt_fn: Optional[Callable[[str], str]] = None,
) -> GateDecision:
    """Decide si l'indexation peut continuer.

    - Pas de code non supporte -> proceed.
    - Code non supporte + allow_unsupported -> proceed (override explicite).
    - Code non supporte + interactif -> on DEMANDE a l'humain (prompt_fn).
    - Code non supporte + non-interactif -> STOP (refus explicite, sur par defaut).
    """
    items = unsupported_items(coverage)
    if not items:
        return GateDecision(True, "Aucun code non supporte ; tous les langages presents sont geres.")

    if allow_unsupported:
        return GateDecision(True, "Code non supporte present mais override (allow_unsupported).")

    if interactive:
        fn = prompt_fn or input
        ans = fn("Du code non supporte est present (voir ci-dessus). Continuer quand meme "
                 "(seuls TS/JS + texte/config seront indexes) ? [o/N] ").strip().lower()
        if ans in ("o", "oui", "y", "yes"):
            return GateDecision(True, "L'humain a choisi de continuer (indexation du sous-ensemble supporte).")
        return GateDecision(False, "L'humain a choisi d'arreter.")

    # Non-interactif et pas d'override -> STOP par defaut.
    langs = ", ".join(sorted({e.language or "?" for e in items}))
    return GateDecision(
        False,
        f"REFUS : code non supporte present ({langs}). "
        f"Relancer avec --allow-unsupported pour n'indexer que les langages supportes, "
        f"ou valider ces langages (Sprint 2).",
    )
