# tests/test_gate.py
#
# Sprint 2 - Phase 0 (Lot C) : verification du gate multi-langage.
# Stdlib pure. Lancer depuis la racine : python tests/test_gate.py

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.languages import (  # noqa: E402
    build_coverage, decide, language_support, unsupported_items,
)
from src.languages.registry import (  # noqa: E402
    ST_VALIDATED, ST_EXPERIMENTAL, ST_UNSUPPORTED, ST_TEXT, ST_CONFIG, ST_IGNORED,
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
    # --- 1. language_support : statut + langage --------------------------- #
    cases = {
        "OpenFrontIO/src/core/AttackExecution.ts": (ST_VALIDATED, "typescript"),
        "proj/app.js": (ST_VALIDATED, "javascript"),
        "proj/script.py": (ST_UNSUPPORTED, "python"),
        "proj/map-generator/main.go": (ST_UNSUPPORTED, "go"),
        "proj/build.sh": (ST_UNSUPPORTED, "bash"),
        "proj/engine.cpp": (ST_UNSUPPORTED, "cpp"),
        "proj/README.md": (ST_TEXT, None),
        "proj/ci.yml": (ST_TEXT, None),
        "proj/package.json": (ST_CONFIG, None),
        "proj/logo.svg": (ST_IGNORED, None),
        "proj/shader.glsl": (ST_IGNORED, None),
        "proj/styles.css": (ST_IGNORED, None),
    }
    for path, expected in cases.items():
        got = language_support(path)
        check(f"language_support('{path}') == {expected}", got == expected)

    # --- 2. Projet CLEAN (TS/JS + texte/config) -> proceed --------------- #
    clean = ["proj/a.ts", "proj/b.tsx", "proj/c.js", "proj/README.md", "proj/package.json",
             "proj/logo.svg"]
    cov_clean = build_coverage(clean)
    check("clean : aucun item non supporte", unsupported_items(cov_clean) == [])
    d = decide(cov_clean, interactive=False, allow_unsupported=False)
    check("clean : decide -> proceed", d.proceed is True)

    # --- 3. Projet avec code non supporte -------------------------------- #
    mixed = ["proj/a.ts", "proj/main.go", "proj/build.sh", "proj/x.py", "proj/README.md"]
    cov_mixed = build_coverage(mixed)
    items = unsupported_items(cov_mixed)
    langs = sorted({e.language for e in items})
    check("mixte : langues non supportees = bash/go/python", langs == ["bash", "go", "python"])

    # non-interactif, pas d'override -> STOP
    d_stop = decide(cov_mixed, interactive=False, allow_unsupported=False)
    check("mixte non-interactif sans override -> STOP", d_stop.proceed is False)

    # override -> proceed
    d_allow = decide(cov_mixed, interactive=False, allow_unsupported=True)
    check("mixte + allow_unsupported -> proceed", d_allow.proceed is True)

    # interactif, humain dit oui -> proceed
    d_yes = decide(cov_mixed, interactive=True, allow_unsupported=False, prompt_fn=lambda _: "o")
    check("mixte interactif 'o' -> proceed", d_yes.proceed is True)

    # interactif, humain dit non -> STOP
    d_no = decide(cov_mixed, interactive=True, allow_unsupported=False, prompt_fn=lambda _: "n")
    check("mixte interactif 'n' -> STOP", d_no.proceed is False)

    # --- 4. total_files coherent ----------------------------------------- #
    check("coverage.total_files == len(paths)", cov_mixed.total_files == len(mixed))

    print("-" * 56)
    print(f"  reussis : {_passed}   echecs : {_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
