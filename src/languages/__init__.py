# src/languages/ -- abstraction multi-langage du RAG (Sprint 2, Phase 0).
#
# API publique : le contrat LanguageProvider + le registre + la classification.
# Import leger (stdlib pure) : aucun tree-sitter / LLM / DB ici.

from __future__ import annotations

from .base import (
    FileCategory,
    LanguageProvider,
    LanguageStatus,
    ScipIndexerSpec,
)
from .registry import (
    LANGUAGE_REGISTRY,
    all_extensions,
    classify_file,
    language_support,
    provider_for_ext,
    provider_for_path,
    validated_languages,
)
from .gate import (
    Coverage,
    GateDecision,
    build_coverage,
    decide,
    format_alert,
    format_matrix,
    unsupported_items,
)

__all__ = [
    "FileCategory",
    "LanguageProvider",
    "LanguageStatus",
    "ScipIndexerSpec",
    "LANGUAGE_REGISTRY",
    "all_extensions",
    "classify_file",
    "language_support",
    "provider_for_ext",
    "provider_for_path",
    "validated_languages",
    # gate
    "Coverage",
    "GateDecision",
    "build_coverage",
    "decide",
    "format_alert",
    "format_matrix",
    "unsupported_items",
]
