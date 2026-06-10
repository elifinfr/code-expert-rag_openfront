# src/languages/typescript.py
#
# Provider TypeScript (.ts/.tsx). status = VALIDATED : le chunker TS est prouve
# par l'existant (Brique 1) et re-confirme par golden au Lot B.
#
# Les node-types ci-dessous sont repris VERBATIM des constantes globales actuelles
# de src/vectordb_indexer.py (SPLITTABLE_NODE_TYPES / LEAF_NODE_TYPES) et de la
# liste de _get_dynamic_name. Toute divergence casserait la non-regression : NE PAS
# modifier sans regenerer le golden.

from __future__ import annotations

from .base import LanguageProvider, ScipIndexerSpec

# --- Constantes cAST (src/vectordb_indexer.py:59-60) ---
_MAX_SPLIT_LINES = 50
_MIN_MERGE_LINES = 10

# --- node-types tree-sitter (src/vectordb_indexer.py:90-99) ---
_SPLITTABLE = frozenset({
    "class_declaration", "method_definition", "if_statement",
    "for_statement", "while_statement", "switch_statement",
    "try_statement", "lexical_declaration", "function_declaration",
    "interface_declaration",
})
_LEAF = frozenset({
    "public_field_definition", "private_field_definition",
    "protected_field_definition", "import_statement", "comment",
})
# noeuds porteurs d'un champ 'name' (src/vectordb_indexer.py:166-167)
_NAME_FIELD = frozenset({
    "class_declaration", "method_definition", "function_declaration",
    "public_field_definition", "private_field_definition", "protected_field_definition",
})

PROVIDER = LanguageProvider(
    name="typescript",
    extensions=frozenset({".ts", ".tsx"}),
    ts_language="typescript",
    splittable_node_types=_SPLITTABLE,
    leaf_node_types=_LEAF,
    name_field_node_types=_NAME_FIELD,
    max_lines=_MAX_SPLIT_LINES,
    min_lines=_MIN_MERGE_LINES,
    status="VALIDATED",
    golden_dir="tests/golden/typescript",
    scip_indexer=ScipIndexerSpec(
        docker_image="code-expert-indexers:latest",
        index_cmd=("scip-typescript", "index", "--output", "/out/index.scip", "--no-progress-bar"),
        needs_config="tsconfig.json",
    ),
)
