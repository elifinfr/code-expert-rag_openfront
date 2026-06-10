# src/languages/javascript.py
#
# Provider JavaScript (.js/.jsx/.mjs). status = VALIDATED.
#
# IMPORTANT : le code actuel (src/vectordb_indexer.py) utilise les MEMES sets de
# node-types pour TS et JS (constantes globales partagees) ; seule la grammaire
# tree-sitter differe ('javascript'). On reprend donc ici exactement les memes
# valeurs que le provider TypeScript pour garantir l'identite de comportement
# (non-regression prouvee au Lot B). NE PAS modifier sans regenerer le golden.

from __future__ import annotations

from .base import LanguageProvider, ScipIndexerSpec

_MAX_SPLIT_LINES = 50
_MIN_MERGE_LINES = 10

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
_NAME_FIELD = frozenset({
    "class_declaration", "method_definition", "function_declaration",
    "public_field_definition", "private_field_definition", "protected_field_definition",
})

PROVIDER = LanguageProvider(
    name="javascript",
    extensions=frozenset({".js", ".jsx", ".mjs"}),
    ts_language="javascript",
    splittable_node_types=_SPLITTABLE,
    leaf_node_types=_LEAF,
    name_field_node_types=_NAME_FIELD,
    max_lines=_MAX_SPLIT_LINES,
    min_lines=_MIN_MERGE_LINES,
    status="VALIDATED",
    golden_dir="tests/golden/javascript",
    scip_indexer=ScipIndexerSpec(
        docker_image="code-expert-indexers:latest",
        index_cmd=("scip-typescript", "index", "--output", "/out/index.scip", "--no-progress-bar"),
        needs_config="tsconfig.json",
    ),
)
