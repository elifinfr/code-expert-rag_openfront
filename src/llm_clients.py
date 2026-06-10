# src/llm_clients.py
#
# Fabrique centralisee des clients LLM/embeddings, cables sur LM Studio
# (API OpenAI-compatible). Reutilise par l'ETL et par l'agent.
#
# POINT CRITIQUE : check_embedding_ctx_length=False
#   Par defaut, OpenAIEmbeddings tokenise avec tiktoken et envoie des
#   tableaux de token-ids (entiers) au serveur. LM Studio attend du TEXTE.
#   Sans ce flag -> erreurs 400 / embeddings incoherents.

import configparser
from pathlib import Path
from functools import lru_cache

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.ini"


@lru_cache(maxsize=1)
def _cfg():
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf-8")
    return config["DEFAULT"]


try:
    from langchain_core.embeddings import Embeddings as _BaseEmbeddings
except Exception:  # fallback si import indispo
    _BaseEmbeddings = object


class _PrefixedEmbeddings(_BaseEmbeddings):
    """Enveloppe : applique un prefixe de tache cote REQUETE uniquement.
    nomic-embed-code attend 'Represent this query for searching relevant code: '
    pour les requetes, et du code/texte BRUT pour les documents indexes."""

    def __init__(self, inner, query_prefix: str = ""):
        self._inner = inner
        self._qp = (query_prefix + " ") if query_prefix else ""

    def embed_documents(self, texts):
        return self._inner.embed_documents(texts)

    def embed_query(self, text):
        return self._inner.embed_query(self._qp + text)


def get_embeddings():
    """Embeddings via serveur dedie (llama-server / nomic-embed-code).

    base_url/model/cle lus depuis EMBEDDING_* (fallback sur LLM_* si absents,
    pour rester compatible avec un setup mono-endpoint LM Studio).
    """
    from langchain_openai import OpenAIEmbeddings
    c = _cfg()
    base_url = c.get("EMBEDDING_BASE_URL", c["LLM_BASE_URL"])
    api_key = c.get("EMBEDDING_API_KEY", c.get("LLM_API_KEY", "lm-studio"))
    inner = OpenAIEmbeddings(
        model=c["EMBEDDING_MODEL"],
        base_url=base_url,
        api_key=api_key,
        check_embedding_ctx_length=False,
    )
    return _PrefixedEmbeddings(inner, c.get("EMBEDDING_QUERY_PREFIX", ""))


def get_chat(temperature: float = 0.0, max_tokens: int | None = None,
             timeout: float | None = None, max_retries: int = 2):
    """LLM de generation via LM Studio.

    timeout : delai max par requete (s). Indispensable pour l'ETL fiches : sans lui,
    une generation qui reste a 0% (overcommit VRAM) bloque le process indefiniment.
    """
    from langchain_openai import ChatOpenAI
    c = _cfg()
    return ChatOpenAI(
        model=c["GENERATION_MODEL"],
        base_url=c["LLM_BASE_URL"],
        api_key=c.get("LLM_API_KEY", "lm-studio"),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=max_retries,
    )


def embedding_dimension() -> int:
    """Detecte dynamiquement la dimension de l'embedder courant."""
    return len(get_embeddings().embed_query("probe"))
