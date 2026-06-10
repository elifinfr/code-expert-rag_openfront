# src/agent_react.py
#
# Brique 2 : Agent ReAct (pattern poc_131 "Faker", create_agent) cable sur LM Studio.
# - Strategie Top-Down : fiches (concept) -> drill-down (code/graphe/config) si besoin.
# - Outils BORNES (top_n petit + troncature) pour ne JAMAIS saturer le contexte.
# - Generation = qwen3-4b-instruct (sans thinking) ; Embeddings = nomic-embed-code (:8090).
#
# Reranker (bge) optionnel : actif seulement si sentence-transformers est installe.

from __future__ import annotations
import configparser
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Literal

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from langchain_chroma import Chroma
from langchain_neo4j import Neo4jGraph
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from .llm_clients import get_chat, get_embeddings

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
#  Config & clients                                                           #
# --------------------------------------------------------------------------- #
def _cfg():
    c = configparser.ConfigParser()
    c.read(ROOT / "config.ini", encoding="utf-8")
    return c["DEFAULT"]


_C = _cfg()
EMBEDDINGS = get_embeddings()
LLM = get_chat(temperature=0)                       # tool-calling + synthese
LLM_SYNTH = get_chat(temperature=0, max_tokens=1024)

QDRANT = QdrantVectorStore(
    client=QdrantClient(host=_C["QDRANT_HOST"], port=int(_C["QDRANT_PORT"])),
    collection_name=_C["QDRANT_COLLECTION_NAME"],
    embedding=EMBEDDINGS,
    content_payload_key="fiche_md",
)
CHROMA = Chroma(
    persist_directory=str(ROOT / _C["VECTORDB_PATH"]),
    embedding_function=EMBEDDINGS,
)
GRAPH = Neo4jGraph(url=_C["NEO4J_URI"], username=_C["NEO4J_USER"], password=_C["NEO4J_PASSWORD"])
JSON_DB = str(ROOT / "json_db.sqlite")


def _trunc(txt: str, n: int) -> str:
    txt = txt or ""
    return txt if len(txt) <= n else txt[:n] + " […]"


# --------------------------------------------------------------------------- #
#  Outils BORNES (les 4 sources)                                              #
# --------------------------------------------------------------------------- #
@tool
def query_qdrant_fiches(query: str) -> str:
    """OUTIL CONCEPTUEL (Phase 1, Top-Down). Donne les fiches de haut niveau d'un fichier
    (role + structure). A utiliser EN PREMIER pour toute question, pour s'orienter."""
    log.info(f"[fiches] {query}")
    docs = QDRANT.similarity_search(query, k=3)
    if not docs:
        return "Aucune fiche pertinente."
    return "\n\n".join(
        f"# Fiche : {d.metadata.get('relative_path')}\n{_trunc(d.page_content, 700)}"
        for d in docs
    )


@tool
def query_chroma_chunks(query: str, file_filter: Optional[List[str]] = None) -> str:
    """OUTIL CODE (Drill-Down). Renvoie des extraits de code source reels.
    file_filter : liste de chemins (ex: ['OpenFrontIO/src/core/...']) pour cibler des fichiers
    precis reperes via les fiches. Utiliser des slashs '/'."""
    log.info(f"[chunks] {query} filter={file_filter}")
    kwargs = {"k": 4}
    if file_filter:
        kwargs["filter"] = {"source_file": {"$in": file_filter}}
    docs = CHROMA.similarity_search(query, **kwargs)
    if not docs:
        return "Aucun extrait de code pertinent (meme apres filtrage)."
    return "\n\n".join(
        f"--- {d.metadata.get('source_file')} (L{d.metadata.get('start_line')}) ---\n{_trunc(d.page_content, 800)}"
        for d in docs
    )


class _Entities(BaseModel):
    names: List[str] = Field(default_factory=list,
                             description="Noms exacts d'entites de code (classes, fonctions, methodes) cites dans la question.")


_CYPHER = """
UNWIND $names AS nm MATCH (n) WHERE n.name = nm
OPTIONAL MATCH (n)-[r]->(o) OPTIONAL MATCH (n)<-[l]-(i)
RETURN n.name AS Node, labels(n)[0] AS Type,
 collect(DISTINCT type(r)+'->'+coalesce(o.name,''))[..6] AS Out,
 collect(DISTINCT coalesce(i.name,'')+'->'+type(l))[..6] AS In LIMIT 15
"""


@tool
def query_neo4j_graph(question: str) -> str:
    """OUTIL STRUCTUREL (Drill-Down). Relations entre entites : 'qui appelle quoi',
    dependances, heritage. Donne-lui la question contenant des noms de classes/fonctions."""
    log.info(f"[graph] {question}")
    try:
        ents = LLM.with_structured_output(_Entities).invoke(
            f"Extrais les noms d'entites de code (classes/fonctions/methodes) de : {question}"
        )
        names = [n for n in (ents.names or []) if n]
    except Exception as e:
        log.warning(f"extraction entites KO: {e}")
        names = []
    if not names:
        return "Aucune entite de code identifiee dans la question."
    rows = GRAPH.query(_CYPHER, params={"names": names})
    if not rows:
        return f"Entites {names} non trouvees dans le graphe."
    out = []
    for r in rows:
        outs = [x for x in r.get("Out", []) if x and not x.endswith("->")]
        ins = [x for x in r.get("In", []) if x and not x.startswith("->")]
        out.append(f"{r['Type']} {r['Node']} | sort: {outs[:5]} | entrant: {ins[:5]}")
    return _trunc("\n".join(out), 1500)


@tool
def query_json_db(query: str) -> str:
    """OUTIL CONFIG (Drill-Down). EXCLUSIVEMENT pour package.json / dependances / config JSON."""
    log.info(f"[json] {query}")
    con = sqlite3.connect(JSON_DB)
    try:
        rows = con.execute(
            "SELECT relative_path, content FROM json_files WHERE relative_path LIKE ? OR content LIKE ? LIMIT 3",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return "Aucun fichier JSON pertinent."
    # NE PAS dumper le contenu entier : extrait borne.
    return "\n\n".join(f"--- {p} ---\n{_trunc(c, 600)}" for p, c in rows)


TOOLS = [query_qdrant_fiches, query_chroma_chunks, query_neo4j_graph, query_json_db]


# --------------------------------------------------------------------------- #
#  Agent ReAct                                                                #
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """Tu es un assistant expert de la base de code OpenFront.io. Tu reponds en suivant une strategie 'Top-Down' STRICTE.

STRATEGIE OBLIGATOIRE :
1. Pour TOUTE nouvelle question, ton PREMIER appel est `query_qdrant_fiches` (les fiches conceptuelles t'orientent et te donnent les chemins de fichiers pertinents).
2. Si les fiches suffisent a repondre, reponds.
3. Sinon, DRILL-DOWN avec les outils specialises :
   - `query_chroma_chunks` (avec `file_filter` = les chemins reperes dans les fiches) pour voir le CODE reel.
   - `query_neo4j_graph` pour les RELATIONS (qui appelle quoi, heritage).
   - `query_json_db` pour package.json / dependances.

REGLES ANTI-HALLUCINATION :
- Ne reponds JAMAIS sans contexte d'outil. N'invente rien depuis tes connaissances generales.
- Cite tes sources (nom de fichier, 'selon le graphe', etc.).
- Si rien de pertinent n'est trouve apres fiches + drill-down, dis : "Aucune information pertinente trouvee dans la base de code indexee."
- Sois concis et factuel.
"""

AGENT = create_agent(
    model=LLM,
    tools=TOOLS,
    system_prompt=SYSTEM_PROMPT,
    checkpointer=InMemorySaver(),
)


def query(question: str, thread_id: str = "cli") -> str:
    cfg = {"configurable": {"thread_id": thread_id}, "recursion_limit": 12}
    res = AGENT.invoke({"messages": [HumanMessage(content=question)]}, cfg)
    return res["messages"][-1].content


def run_interactive():
    print("Agent Code Expert OpenFront (Ctrl-C ou 'exit' pour quitter).")
    print("Sources : fiches(Qdrant) | code(Chroma) | graphe(Neo4j) | config(SQLite)\n")
    while True:
        try:
            q = input("Vous : ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAu revoir.")
            return
        if q.lower() in ("exit", "quit", "quitter", ""):
            if q == "":
                continue
            print("Au revoir.")
            return
        cfg = {"configurable": {"thread_id": "interactive"}, "recursion_limit": 12}
        try:
            for step in AGENT.stream({"messages": [HumanMessage(content=q)]}, cfg, stream_mode="values"):
                step["messages"][-1].pretty_print()
        except Exception as e:
            log.error(f"Echec agent : {e}", exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
    run_interactive()
