# src/languages/base.py
#
# Sprint 2 - Phase 0 (Lot A) : abstraction du contrat "LanguageProvider".
#
# Un LanguageProvider decrit TOUT ce dont le pipeline a besoin pour traiter un
# langage a qualite constante :
#   - CHUNKER : parametres tree-sitter dedies (grammaire + node-types splittables
#     /feuilles/porteurs-de-nom), repris VERBATIM par le RecursiveASTSplitter.
#   - GRAPHE  : comment lancer l'indexeur SCIP (en Docker) pour ce langage.
#   - GATE    : statut de validation (VALIDATED vs EXPERIMENTAL) + dossier golden.
#
# Principe non negociable (cf. SPRINT2_PLAN) : un langage est VALIDATED (chunker +
# graphe a parite TS/JS, prouve par golden tests) ou REFUSE explicitement. Pas de
# fallback generique degrade. Ce module est volontairement en STDLIB PURE (aucun
# import lourd : ni tree-sitter, ni LLM, ni DB) pour rester importable partout,
# y compris dans `rag.py doctor`.

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional


class FileCategory(str, Enum):
    """Categorie d'un fichier vis-a-vis du pipeline.

    Separe le couplage-langage (CODE) des sources agnostiques deja gerees
    ailleurs (TEXT -> text splitter/fiches, CONFIG -> json_indexer/SQLite).
    UNKNOWN = type non reconnu (le gate decidera : refus explicite).
    """

    CODE = "CODE"        # extension couverte par un LanguageProvider (chunker AST + graphe)
    TEXT = "TEXT"        # .md/.mdx/.yml/.yaml -> text splitter robuste (agnostique)
    CONFIG = "CONFIG"    # .json -> json_indexer (SQLite)
    UNKNOWN = "UNKNOWN"  # le reste -> non gere


# Statut de validation d'un langage. Seul VALIDATED autorise l'indexation.
LanguageStatus = Literal["EXPERIMENTAL", "VALIDATED"]


@dataclass(frozen=True)
class ScipIndexerSpec:
    """Decrit comment produire un index SCIP pour un langage (execute en Docker/Linux).

    Renseigne au Lot D (conteneurisation + loader SCIP->Neo4j). Reste None tant
    que le backbone graphe du langage n'est pas branche.
    """

    docker_image: str                  # ex: "code-expert-indexers:latest"
    index_cmd: tuple[str, ...]         # template de commande scip-<lang> (cf. scip_runner)
    needs_config: Optional[str] = None  # fichier requis dans le projet, ex "tsconfig.json"


@dataclass(frozen=True)
class LanguageProvider:
    """Contrat unique decrivant le traitement d'un langage de bout en bout."""

    # --- Identite -------------------------------------------------------------
    name: str                              # identifiant canonique, ex "typescript"
    extensions: frozenset[str]             # ex frozenset({".ts", ".tsx"})

    # --- CHUNKER (tree-sitter) ------------------------------------------------
    ts_language: str                       # grammaire tree-sitter-language-pack
    splittable_node_types: frozenset[str]  # noeuds re-decoupables si trop gros
    leaf_node_types: frozenset[str]        # noeuds-feuilles chunkes tels quels
    name_field_node_types: frozenset[str]  # noeuds dont on lit child_by_field_name('name')
    max_lines: int                         # seuil de split (MAX_SPLIT_LINES)
    min_lines: int                         # seuil de merge (MIN_MERGE_LINES)

    # --- GATE / validation ----------------------------------------------------
    status: LanguageStatus                 # VALIDATED ou EXPERIMENTAL
    golden_dir: str                        # dossier des fixtures/sorties attendues

    # --- GRAPHE (Lot D) -------------------------------------------------------
    scip_indexer: Optional[ScipIndexerSpec] = None

    # Noeuds "enveloppe" dont le nom est porte par un enfant nomme (ex Python
    # decorated_definition -> function_definition). Vide par defaut (TS/JS inchanges).
    unwrap_name_node_types: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.extensions:
            raise ValueError(f"LanguageProvider '{self.name}': extensions vide.")
        for ext in self.extensions:
            if not ext.startswith("."):
                raise ValueError(
                    f"LanguageProvider '{self.name}': extension '{ext}' doit commencer par '.'"
                )
        if self.min_lines > self.max_lines:
            raise ValueError(
                f"LanguageProvider '{self.name}': min_lines ({self.min_lines}) "
                f"> max_lines ({self.max_lines})."
            )

    @property
    def is_validated(self) -> bool:
        return self.status == "VALIDATED"
