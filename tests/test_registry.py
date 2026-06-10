# tests/test_registry.py
#
# Sprint 2 - Phase 0 (Lot A) : verification du registre de langages.
# Stdlib pure (aucune dependance lourde). Lancer depuis la racine du projet :
#     python tests/test_registry.py
#
# Sort en code 0 si tout passe, 1 sinon.

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.languages import (  # noqa: E402
    FileCategory,
    LANGUAGE_REGISTRY,
    all_extensions,
    classify_file,
    provider_for_ext,
    provider_for_path,
    validated_languages,
)

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"[ OK ] {label}")
    else:
        _failed += 1
        print(f"[FAIL] {label}")


def main() -> int:
    # --- 1. Registre peuple, TS/JS presents et VALIDATED --------------------
    check("registre contient typescript + javascript",
          {"typescript", "javascript"} <= set(LANGUAGE_REGISTRY))
    check("typescript VALIDATED", LANGUAGE_REGISTRY["typescript"].is_validated)
    check("javascript VALIDATED", LANGUAGE_REGISTRY["javascript"].is_validated)
    check("validated_languages() = TS+JS",
          set(validated_languages()) == {"typescript", "javascript"})

    # --- 2. Resolution extension -> provider --------------------------------
    for ext, lang in [(".ts", "typescript"), (".tsx", "typescript"),
                      (".js", "javascript"), (".jsx", "javascript"),
                      (".mjs", "javascript")]:
        p = provider_for_ext(ext)
        check(f"provider_for_ext('{ext}') -> {lang}", p is not None and p.name == lang)

    check("provider_for_ext sans point ('ts')",
          (provider_for_ext("ts") or None) is LANGUAGE_REGISTRY["typescript"])
    check("provider_for_ext insensible a la casse ('.TS')",
          (provider_for_ext(".TS") or None) is LANGUAGE_REGISTRY["typescript"])
    check("provider_for_ext('.py') -> None (pas supporte ici)",
          provider_for_ext(".py") is None)

    # --- 3. Resolution chemin (slash ET antislash, prefixe projet) ----------
    check("provider_for_path posix .ts",
          provider_for_path("OpenFrontIO/src/core/execution/AttackExecution.ts").name == "typescript")
    check("provider_for_path antislash .tsx",
          provider_for_path(r"OpenFrontIO\src\client\App.tsx").name == "typescript")
    check("provider_for_path .py -> None", provider_for_path("scripts/build.py") is None)

    # --- 4. Classification CODE / TEXT / CONFIG / UNKNOWN -------------------
    cases = {
        "src/a.ts": FileCategory.CODE,
        "src/a.js": FileCategory.CODE,
        "README.md": FileCategory.TEXT,
        "docs/guide.mdx": FileCategory.TEXT,
        "ci/config.yml": FileCategory.TEXT,
        "ci/config.yaml": FileCategory.TEXT,
        "package.json": FileCategory.CONFIG,
        "scripts/build.py": FileCategory.UNKNOWN,
        "Makefile": FileCategory.UNKNOWN,
        "image.png": FileCategory.UNKNOWN,
    }
    for path, expected in cases.items():
        got = classify_file(path)
        check(f"classify_file('{path}') == {expected.value}", got == expected)

    # --- 5. all_extensions coherent -----------------------------------------
    check("all_extensions() == union des extensions providers",
          all_extensions() == frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs"}))

    # --- 6. Invariants providers --------------------------------------------
    ts = LANGUAGE_REGISTRY["typescript"]
    check("TS node-types non vides",
          bool(ts.splittable_node_types) and bool(ts.leaf_node_types))
    check("TS/JS partagent les memes node-types (verbatim)",
          LANGUAGE_REGISTRY["typescript"].splittable_node_types
          == LANGUAGE_REGISTRY["javascript"].splittable_node_types
          and LANGUAGE_REGISTRY["typescript"].leaf_node_types
          == LANGUAGE_REGISTRY["javascript"].leaf_node_types)
    check("TS max/min lines = 50/10", ts.max_lines == 50 and ts.min_lines == 10)
    check("scip_indexer renseigne (Lot D)",
          ts.scip_indexer is not None
          and ts.scip_indexer.docker_image == "code-expert-indexers:latest")

    print("-" * 56)
    print(f"  reussis : {_passed}   echecs : {_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
