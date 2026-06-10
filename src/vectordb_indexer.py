"""
Module d'Indexation Vectorielle (Chunks) pour ChromaDB.

Version SOTA (POC 122) :
- Lit le 'project_manifest_detailed.json' (filtré score >= 5).
- SÉPARATION SOTA :
  - Utilise le parser AST 'typescript' pour les fichiers .ts/.tsx.
  - Utilise le parser AST 'javascript' pour les fichiers .js/.jsx.
- Utilise 'RecursiveCharacterTextSplitter' pour les .md/.yml.
- Ignore les .json (gérés par json_indexer.py).
"""

import logging
import sys
import shutil
import re
import configparser
import json 
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, List, Optional, Set, Dict

# --- Fondations Tree-Sitter (Validées POC 23) ---
try:
    from tree_sitter import Parser, Language, Node
    from tree_sitter_language_pack import get_language
except ImportError:
    logging.error("ÉCHEC : 'tree-sitter' ou 'tree-sitter-language-pack' non installés.")
    logging.error("Veuillez exécuter : pip install tree-sitter tree-sitter-language-pack")
    sys.exit(1)

# --- Fondations LangChain (Validées POC 121) ---
try:
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    # AJOUT (POC 121) : Splitter textuel robuste
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from .llm_clients import get_embeddings
except ImportError:
    logging.error("ÉCHEC : Dépendances LangChain manquantes.")
    logging.critical("Avez-vous oublié d'exécuter 'pip install -r requirements.txt' ?")
    sys.exit(1)

# --- Registre multi-langage (Sprint 2, Phase 0) : stdlib pure, hors try langchain ---
from .languages import (
    FileCategory,
    LanguageProvider,
    classify_file,
    provider_for_path,
)

# =============================================================================
# --- 1. CONFIGURATION ET CONSTANTES FONDAMENTALES ---
# =============================================================================

log = logging.getLogger(__name__)

def setup_logging():
    """Configure une journalisation détaillée (POUR MODE STANDALONE)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# --- Constantes de l'algorithme cAST (Validées POC 35) ---
MAX_SPLIT_LINES = 50
MIN_MERGE_LINES = 10

# --- MODIFICATION (POC 122) : Lecture depuis config.ini ---
config = configparser.ConfigParser()
config_path = Path(__file__).resolve().parent.parent / 'config.ini'

if not config_path.exists():
    log.critical(f"Fichier de configuration introuvable à {config_path}")
    log.critical("Assurez-vous que config.ini est à la racine du projet.")
    sys.exit(1)
config.read(config_path)
defaults = config['DEFAULT']

EMBEDDING_MODEL_NAME = defaults.get('EMBEDDING_MODEL')
CHROMA_PERSIST_DIR = defaults.get('VECTORDB_PATH')
SOURCE_DIR_PATH = defaults.get('CODE_DIRECTORY') # Utilisé comme racine pour résoudre les chemins

# Validation de la configuration
if not all([EMBEDDING_MODEL_NAME, CHROMA_PERSIST_DIR, SOURCE_DIR_PATH]):
    log.critical("Erreur: EMBEDDING_MODEL, VECTORDB_PATH, ou CODE_DIRECTORY est manquant dans config.ini")
    sys.exit(1)

# --- AJOUT (POC 121) : Chemin du Manifest ---
MANIFEST_FILE = Path("project_manifest_detailed.json")
PROJECT_ROOT = Path(config_path.resolve().parent) # Racine du projet
# ----------------------------------------


# --- Sprint 2, Phase 0 (Lot B) ---
# Les node-types tree-sitter (splittable / leaf / name_field) ne sont plus des
# constantes globales : ils sont portes par chaque LanguageProvider
# (cf. src/languages/). Le RecursiveASTSplitter les lit depuis le provider.
# MAX_SPLIT_LINES / MIN_MERGE_LINES restent comme valeurs cAST de reference,
# mais chaque provider porte desormais ses propres max_lines / min_lines.

# =============================================================================
# --- 2. REPRÉSENTATION DES DONNÉES (Stable - POC 35) ---
# =============================================================================

@dataclass
class SemanticChunk:
    """Représente une unité sémantique atomique (pré-LangChain)."""
    chunk_type: str
    hierarchical_name: str
    source_text: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    node: Node = field(compare=False)
    jsdoc_text: Optional[str] = None
    jsdoc_semantic_sexp: Optional[str] = None

# =============================================================================
# --- 3. BRIQUE "SPLIT" (Modifiée POC 122) ---
# =============================================================================

class RecursiveASTSplitter:
    """
    Implémente la phase "Split" de cAST.
    MODIFIÉ (POC 122) : Accepte la langue (JS ou TS) à l'initialisation.
    """
    
    def __init__(self, provider: LanguageProvider):
        log.info(f"Initialisation du RecursiveASTSplitter pour : {provider.name}")
        self.provider = provider
        try:
            self.LANG = get_language(provider.ts_language)
        except Exception as e:
            log.critical(f"Échec du chargement du parser tree-sitter pour '{provider.ts_language}'.")
            log.critical("Assurez-vous que 'tree-sitter-language-pack' est à jour.")
            raise e

        # node-types portes par le provider (plus de constantes globales)
        self.SPLITTABLE = provider.splittable_node_types
        self.LEAF = provider.leaf_node_types
        self.NAME_FIELD = provider.name_field_node_types
        self.UNWRAP = provider.unwrap_name_node_types  # ex Python decorated_definition

        # JSDoc : specifique TS/JS (les seuls providers a ce stade). Pour un futur
        # langage sans commentaire-doc '/** */', ceci reste inerte (aucun noeud
        # 'comment' JSDoc ne sera trouve).
        self.JSDOC_LANG = get_language('jsdoc')
        self.ts_parser = Parser(self.LANG)  # parser principal du langage
        self.jsdoc_parser = Parser(self.JSDOC_LANG)
        self.MAX_CHUNK_LINES = provider.max_lines
        self.language_name = provider.ts_language

    def _get_node_text(self, n, s) -> str: return s[n.start_byte:n.end_byte].decode("utf-8")
    
    def _parse_jsdoc(self, n, s) -> tuple[str, str]:
        t = self._get_node_text(n, s)
        sexp = str(self.jsdoc_parser.parse(t.encode('utf-8')).root_node)
        if "ERROR" in sexp: logging.warning(f"Parseur JSDoc a une erreur @ L{n.start_point[0]}")
        return t, sexp
        
    def _find_jsdoc_for_node(self, n, s) -> tuple[Optional[str], Optional[str]]:
        sib = n.prev_named_sibling
        if (sib and sib.type == 'comment' and s[sib.start_byte:sib.start_byte + 3].decode('utf-8') == '/**'
                and sib.end_point[0] == n.start_point[0] - 1):
            return self._parse_jsdoc(sib, s)
        return None, None
        
    def _create_chunk(self, n, s, name) -> SemanticChunk:
        jsdoc_t, jsdoc_s = self._find_jsdoc_for_node(n, s)
        return SemanticChunk(chunk_type=n.type, hierarchical_name=name, source_text=self._get_node_text(n, s),
                           start_line=n.start_point[0], end_line=n.end_point[0], start_byte=n.start_byte,
                           end_byte=n.end_byte, node=n, jsdoc_text=jsdoc_t, jsdoc_semantic_sexp=jsdoc_s)
                           
    def _get_dynamic_name(self, n, s) -> str:
        if n.type in self.NAME_FIELD:
            name_n = n.child_by_field_name('name')
            if name_n: return self._get_node_text(name_n, s)
        # Noeud "enveloppe" (ex Python decorated_definition) : nom porte par un
        # enfant nomme (function_definition/class_definition).
        if n.type in self.UNWRAP:
            for c in n.named_children:
                if c.type in self.NAME_FIELD:
                    name_n = c.child_by_field_name('name')
                    if name_n: return self._get_node_text(name_n, s)
        return f"{n.type}_L{n.start_point[0]}"
        
    def _recursive_split(self, n, s, name, chunks):
        nt = n.type
        is_splittable = nt in self.SPLITTABLE
        is_leaf = nt in self.LEAF
        is_chunkable = is_splittable or is_leaf
        h_name = f"{name}.{self._get_dynamic_name(n, s)}"
        
        if not is_chunkable:
            for c in n.named_children: self._recursive_split(c, s, name, chunks)
            return
            
        n_lines = n.end_point[0] - n.start_point[0]
        
        if is_splittable and n_lines > self.MAX_CHUNK_LINES:
            body = n.child_by_field_name('body') or n
            for c in body.named_children: self._recursive_split(c, s, h_name, chunks)
            return
            
        chunks.append(self._create_chunk(n, s, h_name))

    def process_file(self, s, file_name) -> List[SemanticChunk]:
        s_bytes = s.encode("utf-8")
        tree = self.ts_parser.parse(s_bytes) # Utilise le parser (TS ou JS)
        chunks = []
        root_name = file_name.split('.')[0] # Nom de base
        for n in tree.root_node.named_children:
            self._recursive_split(n, s_bytes, root_name, chunks)
        return chunks

# =============================================================================
# --- 4. BRIQUE "MERGE" (Stable - validée POC 35) ---
# =============================================================================

class ChunkMerger:
    """Implémente la phase "Merge" de l'algorithme cAST."""
    
    def __init__(self, min_chunk_lines: int, max_chunk_lines: int):
        self.min_chunk_lines = min_chunk_lines
        self.max_chunk_lines = max_chunk_lines

    def _get_parent_name(self, chunk: SemanticChunk) -> str:
        parts = chunk.hierarchical_name.split('.')
        return ".".join(parts[:-1])
        
    def _merge_buffer_to_chunk(self, buffer: List[SemanticChunk]) -> SemanticChunk:
        first, last = buffer[0], buffer[-1]
        merged_text = "\n\n".join([c.source_text for c in buffer])
        parent_name = self._get_parent_name(first)
        return SemanticChunk(
            chunk_type="merged_block", hierarchical_name=f"{parent_name}.merged_L{first.start_line}_L{last.end_line}",
            source_text=merged_text, start_line=first.start_line, end_line=last.end_line,
            start_byte=first.start_byte, end_byte=last.end_byte, node=first.node, 
            jsdoc_text=first.jsdoc_text, jsdoc_semantic_sexp=first.jsdoc_semantic_sexp)
            
    def merge(self, chunks: List[SemanticChunk]) -> List[SemanticChunk]:
        final_chunks = []
        merge_buffer: List[SemanticChunk] = []
        
        for chunk in chunks:
            chunk_lines = chunk.end_line - chunk.start_line
            
            if chunk_lines >= self.min_chunk_lines:
                if merge_buffer: final_chunks.append(self._merge_buffer_to_chunk(merge_buffer))
                final_chunks.append(chunk); merge_buffer = []
                continue
                
            if not merge_buffer:
                merge_buffer.append(chunk); continue
                
            last_parent = self._get_parent_name(merge_buffer[-1])
            current_parent = self._get_parent_name(chunk)
            new_total_span = chunk.end_line - merge_buffer[0].start_line
            
            if (last_parent != current_parent or new_total_span > self.max_chunk_lines):
                final_chunks.append(self._merge_buffer_to_chunk(merge_buffer))
                merge_buffer = [chunk]
            else:
                merge_buffer.append(chunk)
                
        if merge_buffer: final_chunks.append(self._merge_buffer_to_chunk(merge_buffer))
        return final_chunks

# =============================================================================
# --- 4 bis. POINT D'ENTREE CHUNKING (Sprint 2, Phase 0 - Lot B) ---
# =============================================================================
# Split + merge cAST pour un LanguageProvider donne. Fonction unique reutilisee
# par l'indexeur (CodeIndexer._process_file) ET par le harnais golden
# (tests/golden/run_golden.py) -> une seule source de verite pour le chunking.

_SPLITTER_CACHE: Dict[str, "RecursiveASTSplitter"] = {}
_MERGER_CACHE: Dict[str, "ChunkMerger"] = {}


def chunk_source(provider: LanguageProvider, content: str, file_name: str) -> List[SemanticChunk]:
    """Chunke `content` (code source du langage `provider`) en SemanticChunks.

    Les splitter/merger sont caches par provider (le chargement des grammaires
    tree-sitter est couteux). `file_name` sert de racine au nom hierarchique.
    """
    splitter = _SPLITTER_CACHE.get(provider.name)
    if splitter is None:
        splitter = RecursiveASTSplitter(provider)
        _SPLITTER_CACHE[provider.name] = splitter

    merger = _MERGER_CACHE.get(provider.name)
    if merger is None:
        merger = ChunkMerger(min_chunk_lines=provider.min_lines,
                             max_chunk_lines=provider.max_lines)
        _MERGER_CACHE[provider.name] = merger

    initial_chunks = splitter.process_file(content, file_name)
    return merger.merge(initial_chunks)


# =============================================================================
# --- 5. BRIQUE "INDEXER" (Orchestrateur SOTA - Modifié POC 122) ---
# =============================================================================

class CodeIndexer:
    """Orchestre le pipeline complet : Split, Merge, Embed, et Store."""
    
    def __init__(self, model_name: str, persist_dir: str):
        log.info("Initialisation de l'Orchestrateur 'CodeIndexer' (multi-langage, Sprint 2)...")

        # Les chunkers AST par langage sont resolus via le registre de providers
        # (chunk_source), plus de ts_splitter/js_splitter codes en dur.

        # Splitter textuel robuste pour la categorie TEXT (MD, YML) — agnostique.
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=512, # Taille de chunk plus petite pour les textes
            chunk_overlap=50,
            separators=["\n\n", "\n", " ", ""]
        )

        # Le routage (CODE/TEXT/CONFIG/UNKNOWN) est fait par classify_file ;
        # CODE -> chunker du provider, TEXT -> text_splitter, le reste ignore ici.

        self.persist_dir = persist_dir
        self.vector_store = None
        
        try:
            self.embeddings = get_embeddings()
            log.info(f"Modèle d'embedding '{model_name}' chargé via LM Studio.")
        except Exception as e:
            log.error(f"ÉCHEC CRITIQUE : Impossible d'initialiser les embeddings LM Studio.")
            log.error(f"Vérifiez que LM Studio tourne et expose le modèle '{model_name}'.")
            raise e

    def _normalize_jsdoc(self, jsdoc: str) -> str:
        """
        Nettoie un bloc de JSDoc pour l'embedding.
        """
        # Supprimer /** et */
        clean = re.sub(r'/\*\*|\*/', '', jsdoc)
        # Supprimer * en début de ligne
        clean = re.sub(r'^\s*\*\s?', '', clean, flags=re.MULTILINE)
        # Nettoyer les espaces vides
        clean = '\n'.join(line.strip() for line in clean.split('\n') if line.strip())
        return clean

    def _transform_chunks_to_docs(self, chunks: List[SemanticChunk], relative_path: str) -> List[Document]:
        """
        Convertit SemanticChunks en Documents LangChain avec formatage SOTA
        pour l'embedding (JSDoc + Contexte + Code).
        """
        documents = []
        
        for chunk in chunks:
            parts = []
            
            # 1. Documentation (JSDoc) - Priorité haute pour l'embedding
            if chunk.jsdoc_text:
                normalized_jsdoc = self._normalize_jsdoc(chunk.jsdoc_text)
                if normalized_jsdoc:
                    parts.append(f"// Documentation:\n{normalized_jsdoc}")
            
            # 2. Contexte
            context_parts = []
            context_parts.append(f"File: {relative_path}")
            # Remplacer les '.' par ' > ' pour une meilleure lisibilité sémantique
            hierarchical_name_clean = chunk.hierarchical_name.replace('.', ' > ')
            context_parts.append(f"Chunk: {hierarchical_name_clean}")
            parts.append(f"// Context: {' | '.join(context_parts)}")
            
            # 3. Code source
            parts.append(f"\n{chunk.source_text}")
            
            # Créer le contenu final pour l'embedding
            page_content = "\n\n".join(parts)
            
            # Les métadonnées restent vitales pour le filtrage et le contexte
            metadata = {
                "source_file": relative_path,
                "hierarchical_name": chunk.hierarchical_name,
                "chunk_type": chunk.chunk_type,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "jsdoc_summary": chunk.jsdoc_text.splitlines()[0] if chunk.jsdoc_text else "N/A"
            }
            
            doc = Document(
                page_content=page_content,
                metadata=metadata
            )
            documents.append(doc)
            
        return documents

    # --- Sprint 2, Phase 0 (Lot B) : routage par categorie/langage via le registre ---
    def _process_file(self, file_info: dict, base_dir: Path) -> List[Document]:
        """Exécute le pipeline de chunking approprié selon la categorie du fichier."""

        # Normalisation posix (slashs) pour coherence avec Qdrant/graphe :
        # le source_file servira de cle au file_filter du drill-down de l'agent.
        relative_path = file_info['relative_path'].replace('\\', '/')
        absolute_path = base_dir / relative_path
        category = classify_file(relative_path)

        try:
            # 1. CODE : chunker cAST dedie au langage (resolu via le registre)
            if category == FileCategory.CODE:
                provider = provider_for_path(relative_path)
                if provider is None:
                    # Garde-fou : CODE implique provider non-None (registre coherent).
                    log.error(f"Incoherence registre : categorie CODE sans provider pour {relative_path}")
                    return []
                content = absolute_path.read_text(encoding="utf-8")
                semantic_chunks = chunk_source(provider, content, absolute_path.name)
                if not semantic_chunks:
                    log.warning(f"Aucun chunk cAST ({provider.name}) trouvé dans {relative_path}.")
                    return []
                return self._transform_chunks_to_docs(semantic_chunks, relative_path)

            # 2. TEXT : splitter textuel robuste pour MD, YML (agnostique au langage)
            if category == FileCategory.TEXT:
                log.info(f"Utilisation du TextSplitter (robuste) pour {relative_path}")
                content = absolute_path.read_text(encoding="utf-8")
                text_chunks = self.text_splitter.split_text(content)
                documents = []
                for i, chunk_text in enumerate(text_chunks):
                    # Formatage du contenu SOTA (similaire à _transform_chunks_to_docs)
                    page_content = f"// Context: File: {relative_path} | Chunk: {i+1}\n\n{chunk_text}"
                    metadata = {
                        "source_file": relative_path,
                        "hierarchical_name": f"{absolute_path.name}_chunk_{i+1}",
                        "chunk_type": f"{absolute_path.suffix}_file",
                        "start_line": 0, # Le TextSplitter ne connaît pas les lignes
                        "end_line": 0,
                        "jsdoc_summary": "N/A"
                    }
                    doc = Document(page_content=page_content, metadata=metadata)
                    documents.append(doc)
                return documents

            # 3. CONFIG (.json -> json_indexer/SQLite) / UNKNOWN : ignore par Chroma
            log.debug(f"Fichier ignoré par Chroma (categorie {category.value}) : {relative_path}")
            return []

        except Exception as e:
            log.error(f"Échec du traitement de {relative_path}: {e}")
            return []

    # --- MÉTHODE MISE À JOUR (POC 122) : Lit depuis le manifest ---
    def run_full_indexing(self, source_directory: Path):
        """
        Exécute le pipeline complet en lisant le manifest.
        """
        log.info(f"--- DÉBUT DE L'INDEXATION COMPLÈTE (Chroma SOTA POC 122) ---")
        
        # 1. Lire le Manifest (Source de Vérité)
        if not MANIFEST_FILE.exists():
            log.critical(f"ERREUR : Manifest introuvable : {MANIFEST_FILE}")
            log.critical("Veuillez d'abord exécuter l'Option 1 (Audit) depuis main.py")
            raise FileNotFoundError(f"{MANIFEST_FILE} manquant.")
        
        log.info(f"Lecture du manifest filtré : {MANIFEST_FILE}")
        with open(MANIFEST_FILE, 'r', encoding='utf-8') as f:
            manifest_files: List[Dict] = json.load(f)
        
        log.info(f"{len(manifest_files)} fichiers pertinents à indexer (depuis le manifest).")
        
        # 2. Nettoyer l'ancienne BDD
        persist_path = Path(self.persist_dir)
        if persist_path.exists():
            log.warning(f"Nettoyage de l'ancien répertoire de BDD : {self.persist_dir}")
            shutil.rmtree(self.persist_dir)
        
        # 3. Initialiser le VectorStore
        log.info(f"Création d'une nouvelle base de données à : {self.persist_dir}")
        self.vector_store = Chroma(
            persist_directory=self.persist_dir,
            embedding_function=self.embeddings
        )
        
        total_chunks_added = 0
        
        # 4. Traiter et ajouter les fichiers (basé sur le manifest)
        for file_info in manifest_files:
            relative_path_str = file_info['relative_path']
            log.info(f"Traitement : {relative_path_str}")
            
            # PROJECT_ROOT est la racine absolue (ex: /mnt/d/OPENFRONT/code-expert-rag)
            documents = self._process_file(file_info, PROJECT_ROOT)
            
            if documents:
                self.vector_store.add_documents(documents)
                total_chunks_added += len(documents)
                log.info(f"Ajouté {len(documents)} chunks pour {relative_path_str}.")
        
        log.info(f"--- INDEXATION COMPLÈTE (CHROMA) TERMINÉE ---")
        log.info(f"Total des chunks stockés : {total_chunks_added}")
        log.info(f"Base de données persistée à : {self.persist_dir}")

# =============================================================================
# --- 6. POINT D'ENTRÉE (MODIFIÉ POC 49) ---
# =============================================================================

def index_codebase():
    """
    Point d'entrée principal pour l'indexation, appelé par main.py.
    """
    log.info("Lancement de 'index_codebase' (SOTA POC 122)...")
    
    # SOURCE_DIRECTORY_TO_INDEX (ex: ./OpenFrontIO) est toujours requis
    # pour résoudre les chemins relatifs du manifest
    source_dir_path = Path(SOURCE_DIR_PATH)
    if not source_dir_path.exists():
        log.error(f"ERREUR : Le répertoire source n'existe pas : {source_dir_path}")
        sys.exit(1)

    try:
        indexer = CodeIndexer(
            model_name=EMBEDDING_MODEL_NAME,
            persist_dir=CHROMA_PERSIST_DIR
        )
        
        indexer.run_full_indexing(
            source_directory=source_dir_path
        )
        
    except Exception as e:
        log.error(f"Échec de l'exécution 'index_codebase': {e}")
        log.exception("Trace complète :")
        # Remonter l'erreur à main.py
        raise e

if __name__ == "__main__":
    """
    Exécution en mode standalone pour le débogage.
    """
    setup_logging() # Configurer le logging *uniquement* en standalone
    log.info("Démarrage du module 'vectordb_indexer' en mode standalone...")
    
    # Exécuter la même logique que le point d'entrée
    index_codebase()