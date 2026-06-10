# src/graph_builder/scip_loader.py
#
# Sprint 2 - Phase 0 (Lot D) : loader UNIQUE SCIP -> graphe (nodes/edges).
#
# Transforme un index.scip (produit par n'importe quel indexeur SCIP) en
# nodes/edges compatibles avec CodeGraphBuilder (graph_indexer), donc avec Neo4j
# et l'agent existant. Regle de mapping unique, reutilisable par tous les langages :
#
#   - DEFINITION (role & Definition, non-local, fichier dans le manifest)
#       -> NODE, type via SUFFIXE du symbole : '#'=type, '().'=methode/fonction.
#         (fichiers = nodes File depuis le manifest ; champs/params/locals ignores)
#   - REFERENCE située dans l'enclosing_range d'une definition conteneur F
#       -> arête F -[APPELLE|UTILISE]-> symbole cible (si la cible est un node connu)
#   - membre (Methode) -> son type proprietaire : arête DEFINIT_DANS (via le symbole)
#   - relations SCIP is_implementation -> IMPLEMENTE / HERITE_DE
#
# Pas de 'kind' fiable dans scip-typescript 0.3.15 -> classification par suffixe.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Role bitmask (cf. scip.proto SymbolRole)
ROLE_DEFINITION = 0x1

# --- Parsing des descripteurs SCIP ----------------------------------------- #
# Un symbole = "<scheme> <manager> <package> <version> <descriptors>".
# Les descripteurs encodent la hierarchie avec des suffixes :
#   name/  = namespace   name#  = type     name.  = term/champ
#   name(disamb). = methode      (param) = parametre
_DESC_RE = re.compile(
    r"(?P<name>`[^`]+`|[^/#.:!()`]+)(?:(?P<method>\([^)]*\)\.)|(?P<suffix>[/#.:!]))"
    r"|\((?P<param>[^)]*)\)"
)

# Types de noeuds qu'on materialise (comme ts-morph : types + methodes/fonctions).
KIND_TYPE = "TYPE"        # -> Class / Interface
KIND_METHOD = "METHOD"    # membre d'un type
KIND_FUNCTION = "FUNCTION"  # fonction de module
# (NAMESPACE/FILE, TERM/champ, PARAM, LOCAL -> pas de node)


@dataclass
class ParsedSymbol:
    valid: bool
    kind: Optional[str] = None          # KIND_TYPE / KIND_METHOD / KIND_FUNCTION / other
    name: Optional[str] = None          # nom d'affichage (dernier descripteur)
    owner_type_symbol: Optional[str] = None  # symbole du type proprietaire (pour DEFINIT_DANS)


def _unquote(name: str) -> str:
    if len(name) >= 2 and name[0] == "`" and name[-1] == "`":
        return name[1:-1]
    return name


def parse_symbol(symbol: str) -> ParsedSymbol:
    """Classe un symbole SCIP par le suffixe de son dernier descripteur."""
    if not symbol or symbol.startswith("local "):
        return ParsedSymbol(False)
    parts = symbol.split(" ", 4)
    if len(parts) < 5:
        return ParsedSymbol(False)
    prefix = " ".join(parts[:4]) + " "
    descriptors = parts[4]

    last_name = None
    last_kind = None            # 'type'|'method'|'function'|'term'|'namespace'|'param'
    owner_type_symbol = None
    last_type_end = None        # offset (dans descriptors) de fin du dernier TYPE rencontre
    seen_type_before_last = None

    matches = list(_DESC_RE.finditer(descriptors))
    if not matches:
        return ParsedSymbol(False)

    for i, m in enumerate(matches):
        is_last = (i == len(matches) - 1)
        if m.group("param") is not None:
            kind = "param"
            name = _unquote(m.group("param"))
        elif m.group("method") is not None:
            kind = "method"
            name = _unquote(m.group("name"))
        else:
            suf = m.group("suffix")
            name = _unquote(m.group("name"))
            kind = {"/": "namespace", "#": "type", ".": "term", ":": "meta", "!": "macro"}.get(suf, "other")

        if is_last:
            last_name = name
            last_kind = kind
            # le proprietaire = dernier TYPE rencontre AVANT ce descripteur
            if last_type_end is not None:
                owner_type_symbol = prefix + descriptors[:last_type_end]
        else:
            if kind == "type":
                last_type_end = m.end()

    # Determiner le kind de node
    if last_kind == "type":
        node_kind = KIND_TYPE
    elif last_kind == "method":
        # methode d'un type si un type precede, sinon fonction de module
        node_kind = KIND_METHOD if owner_type_symbol else KIND_FUNCTION
    else:
        node_kind = last_kind  # term/namespace/param/... -> pas un node

    return ParsedSymbol(
        valid=True,
        kind=node_kind,
        name=last_name,
        owner_type_symbol=owner_type_symbol if node_kind == KIND_METHOD else None,
    )


# --- Helpers ranges --------------------------------------------------------- #
def _range_tuple(r) -> Tuple[int, int, int, int]:
    """Normalise un range SCIP en (startLine, startChar, endLine, endChar)."""
    v = list(r)
    if len(v) == 3:  # single-line : [line, startChar, endChar]
        return (v[0], v[1], v[0], v[2])
    return (v[0], v[1], v[2], v[3])


def _contains(outer: Tuple[int, int, int, int], pos: Tuple[int, int]) -> bool:
    (sl, sc, el, ec) = outer
    (l, c) = pos
    if l < sl or l > el:
        return False
    if l == sl and c < sc:
        return False
    if l == el and c > ec:
        return False
    return True


def _span_size(r: Tuple[int, int, int, int]) -> int:
    return (r[2] - r[0]) * 100000 + (r[3] - r[1])


# --- Construction du graphe ------------------------------------------------- #
@dataclass
class ScipGraph:
    nodes: List[dict] = field(default_factory=list)
    edges: List[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def build_graph_from_scip(
    index_path: Path,
    manifest_rel_paths: set[str],
    project_name: str,
) -> ScipGraph:
    """Construit nodes/edges depuis un index.scip, filtre au perimetre du manifest.

    manifest_rel_paths : ensemble de chemins POSIX prefixes (ex 'OpenFrontIO/src/..').
    project_name       : prefixe a appliquer au relative_path SCIP (ex 'OpenFrontIO').
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import scip_pb2  # genere depuis scip.proto

    idx = scip_pb2.Index()
    idx.ParseFromString(Path(index_path).read_bytes())

    # Detection du langage via le scheme SCIP (1er token d'un symbole non-local).
    # Seul TypeScript a la notion d'interface ; pour les autres langages (Python...)
    # tout symbole '#' est une Class et les relations is_implementation = HERITAGE.
    scheme = None
    for _doc in idx.documents:
        for _occ in _doc.occurrences:
            if _occ.symbol and not _occ.symbol.startswith("local "):
                scheme = _occ.symbol.split(" ", 1)[0]
                break
        if scheme:
            break
    detect_interfaces = (scheme == "scip-typescript")

    def canonical(scip_rel: str) -> str:
        return f"{project_name}/{scip_rel}".replace("\\", "/")

    # --- Passe 1 : NODES (definitions) + index symbole->node --------------- #
    # symbol -> node dict ; on garde aussi file + interfaces (relationships).
    sym_node: Dict[str, dict] = {}
    interface_syms: set[str] = set()

    # collecte des relations is_implementation (pour Interface + IMPLEMENTE/HERITE)
    impl_rels: List[Tuple[str, str]] = []  # (source_symbol, target_symbol)

    for doc in idx.documents:
        cfile = canonical(doc.relative_path)
        in_scope = cfile in manifest_rel_paths
        if not in_scope:
            continue

        # SymbolInformation : relations d'implementation/heritage
        for si in doc.symbols:
            for rel in si.relationships:
                if rel.is_implementation:
                    impl_rels.append((si.symbol, rel.symbol))
                    # Cible = interface UNIQUEMENT en TS (Python/autres : heritage de classe)
                    if detect_interfaces:
                        interface_syms.add(rel.symbol)

        # Definitions -> nodes
        for occ in doc.occurrences:
            if not (occ.symbol_roles & ROLE_DEFINITION):
                continue
            ps = parse_symbol(occ.symbol)
            if not ps.valid or ps.kind not in (KIND_TYPE, KIND_METHOD, KIND_FUNCTION):
                continue
            if occ.symbol in sym_node:
                continue
            rng = _range_tuple(occ.range)
            sym_node[occ.symbol] = {
                "_kind": ps.kind,
                "_owner": ps.owner_type_symbol,
                "name": ps.name,
                "filePath": cfile,
                "line": rng[0] + 1,  # 1-based comme ts-morph
                "_symbol": occ.symbol,
            }

    # Affecter les labels de type (Class/Interface/Function/Method)
    def label_for(node: dict) -> str:
        k = node["_kind"]
        if k == KIND_METHOD:
            return "Method"
        if k == KIND_FUNCTION:
            return "Function"
        # TYPE
        return "Interface" if node["_symbol"] in interface_syms else "Class"

    # --- Passe 2 : FILE nodes depuis le manifest --------------------------- #
    nodes: List[dict] = []
    for rel in sorted(manifest_rel_paths):
        nodes.append({
            "id": f"File::{Path(rel).name}::{rel}",
            "name": Path(rel).name,
            "filePath": rel,
            "line": 0,
            "type": "File",
        })

    for sym, n in sym_node.items():
        nodes.append({
            "id": sym,
            "name": n["name"],
            "filePath": n["filePath"],
            "line": n["line"],
            "type": label_for(n),
        })

    # --- Passe 3 : EDGES --------------------------------------------------- #
    edges: List[dict] = []
    eid = 0

    def add_edge(src, tgt, etype, **props):
        nonlocal eid
        e = {"id": f"e_{eid}", "source": src, "target": tgt, "type": etype}
        e.update(props)
        edges.append(e)
        eid += 1

    # 3a. DEFINIT_DANS : methode -> type proprietaire (si les 2 sont des nodes)
    for sym, n in sym_node.items():
        owner = n["_owner"]
        if owner and owner in sym_node:
            add_edge(sym, owner, "DEFINIT_DANS")

    # 3b. IMPLEMENTE / HERITE_DE depuis relationships
    for src, tgt in impl_rels:
        if src in sym_node and tgt in sym_node:
            # Interface cible -> IMPLEMENTE ; sinon (classe parente) -> HERITE_DE
            etype = "IMPLEMENTE" if tgt in interface_syms else "HERITE_DE"
            add_edge(src, tgt, etype)

    # 3c. APPELLE / UTILISE : reference dans l'enclosing_range d'un conteneur
    n_call = 0
    n_use = 0
    for doc in idx.documents:
        cfile = canonical(doc.relative_path)
        if cfile not in manifest_rel_paths:
            continue
        # conteneurs = definitions de ce doc avec enclosing_range (corps)
        containers: List[Tuple[Tuple[int, int, int, int], str]] = []
        for occ in doc.occurrences:
            if (occ.symbol_roles & ROLE_DEFINITION) and len(occ.enclosing_range) > 0:
                if occ.symbol in sym_node:  # seulement nos nodes (methode/fonction/type)
                    containers.append((_range_tuple(occ.enclosing_range), occ.symbol))
        # references -> conteneur le plus interne
        for occ in doc.occurrences:
            if occ.symbol_roles & ROLE_DEFINITION:
                continue
            tgt = occ.symbol
            tnode = sym_node.get(tgt)
            if tnode is None:
                continue  # cible non resolue / hors nodes -> ignore
            r = _range_tuple(occ.range)
            pos = (r[0], r[1])
            # innermost container contenant pos
            best = None
            best_span = None
            for (er, csym) in containers:
                if csym == tgt:
                    continue
                if _contains(er, pos):
                    sp = _span_size(er)
                    if best_span is None or sp < best_span:
                        best = csym
                        best_span = sp
            if best is None:
                continue
            if tnode["_kind"] in (KIND_METHOD, KIND_FUNCTION):
                add_edge(best, tgt, "APPELLE", callLine=r[0] + 1)
                n_call += 1
            elif tnode["_kind"] == KIND_TYPE:
                add_edge(best, tgt, "UTILISE", refLine=r[0] + 1)
                n_use += 1

    stats = {
        "documents": len(idx.documents),
        "nodes_total": len(nodes),
        "nodes_file": sum(1 for n in nodes if n["type"] == "File"),
        "nodes_class": sum(1 for n in nodes if n["type"] == "Class"),
        "nodes_interface": sum(1 for n in nodes if n["type"] == "Interface"),
        "nodes_method": sum(1 for n in nodes if n["type"] == "Method"),
        "nodes_function": sum(1 for n in nodes if n["type"] == "Function"),
        "edges_total": len(edges),
        "edges_appelle": sum(1 for e in edges if e["type"] == "APPELLE"),
        "edges_definit_dans": sum(1 for e in edges if e["type"] == "DEFINIT_DANS"),
        "edges_utilise": sum(1 for e in edges if e["type"] == "UTILISE"),
        "edges_implemente": sum(1 for e in edges if e["type"] == "IMPLEMENTE"),
        "edges_herite": sum(1 for e in edges if e["type"] == "HERITE_DE"),
    }
    return ScipGraph(nodes=nodes, edges=edges, stats=stats)


# --- Dry-run standalone (comptes, sans Neo4j) ------------------------------- #
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parent.parent.parent
    index_path = root / ".scip_out" / "index.scip"
    manifest = json.loads((root / "project_manifest_detailed.json").read_text(encoding="utf-8"))
    rels = {fi["relative_path"].replace("\\", "/") for fi in manifest}
    project_name = "OpenFrontIO"
    g = build_graph_from_scip(index_path, rels, project_name)
    print(json.dumps(g.stats, indent=2))
