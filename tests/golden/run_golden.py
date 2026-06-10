# tests/golden/run_golden.py
#
# Harnais golden du chunker multi-langage (Sprint 2, Phase 0 - Lot B).
#
# Pour chaque LanguageProvider du registre, chunke les fixtures de son golden_dir
# et compare la sortie (chunk_type, hierarchical_name, start_line, end_line) aux
# fichiers expected/*.json. Prouve la NON-REGRESSION du refactor TS/JS et servira
# de filet pour chaque nouveau langage.
#
# Usage (depuis la racine du projet) :
#     python tests/golden/run_golden.py --check     # echoue si une difference
#     python tests/golden/run_golden.py --regen      # regenere les expected/
#
# Dependances : tree-sitter uniquement (via src.vectordb_indexer.chunk_source).
# Aucun LLM / aucune DB. Sort en code 0 si OK, 1 sinon.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.languages import LANGUAGE_REGISTRY  # noqa: E402
from src.vectordb_indexer import chunk_source  # noqa: E402  (expose au Lot B)


def _serialize(chunks) -> list[dict]:
    return [
        {
            "chunk_type": c.chunk_type,
            "hierarchical_name": c.hierarchical_name,
            "start_line": c.start_line,
            "end_line": c.end_line,
        }
        for c in chunks
    ]


def _iter_fixtures(provider):
    """Yield (fixture_path, expected_path) pour les fixtures d'un provider."""
    base = ROOT / provider.golden_dir
    fixtures_dir = base / "fixtures"
    expected_dir = base / "expected"
    if not fixtures_dir.exists():
        return
    for fx in sorted(fixtures_dir.iterdir()):
        if not fx.is_file():
            continue
        if fx.suffix.lower() not in provider.extensions:
            continue
        yield fx, expected_dir / f"{fx.name}.json"


def run(regen: bool) -> int:
    n_ok = 0
    n_fail = 0
    n_files = 0

    for name, provider in LANGUAGE_REGISTRY.items():
        for fx, expected_path in _iter_fixtures(provider):
            n_files += 1
            content = fx.read_text(encoding="utf-8")
            chunks = chunk_source(provider, content, fx.name)
            got = _serialize(chunks)

            if regen:
                expected_path.parent.mkdir(parents=True, exist_ok=True)
                expected_path.write_text(
                    json.dumps(got, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                print(f"[regen] {name}/{fx.name}: {len(got)} chunks")
                continue

            if not expected_path.exists():
                print(f"[FAIL] {name}/{fx.name}: expected absent ({expected_path.name}) "
                      f"-> lancer --regen")
                n_fail += 1
                continue

            expected = json.loads(expected_path.read_text(encoding="utf-8"))
            if got == expected:
                print(f"[ OK ] {name}/{fx.name}: {len(got)} chunks")
                n_ok += 1
            else:
                print(f"[FAIL] {name}/{fx.name}: divergence vs baseline")
                _print_diff(expected, got)
                n_fail += 1

    print("-" * 56)
    if regen:
        print(f"  regenere : {n_files} fixtures")
        return 0
    print(f"  reussis : {n_ok}   echecs : {n_fail}   (fixtures: {n_files})")
    if n_files == 0:
        print("  [FAIL] aucune fixture trouvee.")
        return 1
    return 0 if n_fail == 0 else 1


def _print_diff(expected: list[dict], got: list[dict]) -> None:
    """Affiche les lignes divergentes (comparaison positionnelle)."""
    maxlen = max(len(expected), len(got))
    for i in range(maxlen):
        e = expected[i] if i < len(expected) else None
        g = got[i] if i < len(got) else None
        if e != g:
            print(f"       #{i}: attendu={_fmt(e)}")
            print(f"            obtenu ={_fmt(g)}")


def _fmt(c) -> str:
    if c is None:
        return "<absent>"
    return f"{c['chunk_type']} {c['hierarchical_name']} L{c['start_line']}-L{c['end_line']}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Harnais golden du chunker")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="compare a la baseline (defaut)")
    g.add_argument("--regen", action="store_true", help="regenere les expected/")
    args = ap.parse_args()
    return run(regen=args.regen)


if __name__ == "__main__":
    sys.exit(main())
