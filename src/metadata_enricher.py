# src/metadata_enricher.py
#
# VERSION SOTA (POC 124.D - Éditorial Détaillé)
# - CORRECTION (Fondation Solide) : Remplacement de la limite fixe (200 lignes)
#   par une limite adaptative (SUMMARY_LINE_LIMIT) pour envoyer le code complet
#   pour la majorité des fichiers (100-300 lignes).
# - Prompt SOTA (Validé) : Utilise le prompt "Éditorial Détaillé" pour
#   forcer une analyse exhaustive au lieu d'un échantillonnage.
# -------------------------------------------------------------------------

import logging
import sys
import configparser
import json
import uuid
from pathlib import Path
from typing import List, Dict, Any, Tuple

# --- Imports (Fondations SOTA) ---
try:
    import qdrant_client
    from qdrant_client.http import models
    log_qdrant = logging.getLogger("qdrant_client")
    log_qdrant.setLevel(logging.WARNING)
    
    from langchain_core.messages import HumanMessage
    from .llm_clients import get_embeddings, get_chat
except ImportError:
    logging.critical("ÉCHEC : 'qdrant-client' / 'langchain-openai' non installés.")
    sys.exit(1)

log = logging.getLogger(__name__)

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] - %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

# --- FONDATIONS SOTA (POC 124.D) ---
VECTOR_DIMENSION = 3584
KG_JSON_PATH = Path("knowledge-graph.json")
config_path_global = Path('config.ini').resolve()
PROJECT_ROOT = config_path_global.parent # Racine du projet (ex: /mnt/d/OPENFRONT/code-expert-rag)
AUDIT_JSON_PATH = Path("knowledge-graph-qdrant-audit.json") # Sortie d'audit

MINI_BATCH_SIZE = 8 # Aligne sur parallel=2 de LM Studio (suivi + memoire plus souples)

# --- AJOUT (Fondation Solide) : Limite adaptative ---
# Fichiers <= 400 lignes = 100% code. Fichiers > 400 = tronqués.
SUMMARY_LINE_LIMIT = 400
# ------------------------------------------------

class MetadataEnricher:
    """
    Orchestre le pipeline ETL SOTA (Mini-Batching) pour Qdrant.
    """
    def __init__(self):
        log.info(f"Initialisation de MetadataEnricher (ETL SOTA - Limite {SUMMARY_LINE_LIMIT} lignes)...")
        
        config = self._load_config()
        self.collection_name = config['QDRANT_COLLECTION_NAME']
        
        log.info(f"Initialisation du client Qdrant (Host: {config['QDRANT_HOST']})...")
        self.client = qdrant_client.QdrantClient(
            host=config['QDRANT_HOST'], 
            port=config['QDRANT_PORT']
        )
        
        log.info(f"Initialisation du modèle d'embedding ({config['EMBEDDING_MODEL']}) via LM Studio...")
        self.embedder = get_embeddings()
        # Dimension detectee dynamiquement (robuste au changement d'embedder)
        self.vector_dimension = len(self.embedder.embed_query("probe"))
        log.info(f"Dimension d'embedding detectee : {self.vector_dimension}")

        log.info(f"Initialisation du LLM de génération ({config['GENERATION_MODEL']}) via LM Studio...")
        # max_tokens borne : un editorial de fiche n'a pas besoin de plus.
        # timeout=120s : une generation figee a 0% (overcommit VRAM) leve une erreur
        # au lieu de bloquer tout le pipeline indefiniment. max_retries=1 : on retente
        # une fois (au cas ou un retry passe) puis on bascule en fallback par fichier.
        self.llm = get_chat(temperature=0.1, max_tokens=1200, timeout=120, max_retries=1)
        
    def _load_config(self) -> Dict[str, str]:
        config_path = Path('config.ini').resolve()
        if not config_path.exists():
            log.critical("config.ini introuvable.")
            raise FileNotFoundError("config.ini not found")
        config = configparser.ConfigParser()
        config.read(config_path)
        return config['DEFAULT']

    def setup_qdrant_collection(self, fresh: bool = True):
        """fresh=True : delete+create (rebuild propre). fresh=False : create si absente (resume)."""
        try:
            exists = self.client.collection_exists(self.collection_name)
            if fresh and exists:
                log.warning(f"Rebuild : suppression de la collection '{self.collection_name}'")
                self.client.delete_collection(self.collection_name)
                exists = False
            if not exists:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=self.vector_dimension,
                        distance=models.Distance.COSINE
                    )
                )
                log.info(f"Collection Qdrant '{self.collection_name}' créée (dim={self.vector_dimension}).")
            else:
                log.info(f"Collection Qdrant '{self.collection_name}' conservée (mode reprise).")
        except Exception as e:
            log.critical(f"Échec du setup de la collection Qdrant : {e}")
            raise e

    def _existing_relative_paths(self) -> set:
        """Chemins relatifs déjà présents dans Qdrant (pour la reprise / skip)."""
        done = set()
        try:
            if not self.client.collection_exists(self.collection_name):
                return done
            offset = None
            while True:
                points, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    with_payload=True, with_vectors=False, limit=256, offset=offset,
                )
                for p in points:
                    rp = (p.payload or {}).get("relative_path")
                    if rp:
                        done.add(rp)
                if offset is None:
                    break
        except Exception as e:
            log.warning(f"Lecture des points existants impossible (reprise ignorée) : {e}")
        return done

    def _load_kg_data(self) -> Dict[str, List[Dict]]:
        """
        Charge le KG JSON (propre) et groupe par fichier.
        """
        if not KG_JSON_PATH.exists():
            log.critical(f"{KG_JSON_PATH} introuvable.")
            raise FileNotFoundError(f"{KG_JSON_PATH} not found")
        
        log.info(f"Lecture de la source de vérité structurelle (PROPRE) : {KG_JSON_PATH}")
        with open(KG_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        nodes_by_file: Dict[str, List[Dict]] = {}
        file_node_count = 0
        
        for node in data.get('nodes', []):
            file_path = node.get('filePath')
            if not file_path:
                continue
            
            if node['type'] not in ('File', 'Config'):
                continue
            
            file_node_count += 1
            file_path = file_path.replace('\\', '/')
            
            child_nodes = [
                n for n in data.get('nodes', [])
                if n.get('filePath', '').replace('\\', '/') == file_path and n['type'] not in ('File', 'Config')
            ]
            
            nodes_by_file[file_path] = child_nodes
            
        log.info(f"Trouvé {file_node_count} fichiers uniques (:File, :Config) à enrichir.")
        return nodes_by_file

    # --- === DEBUT DE LA MODIFICATION (PROMPT "ÉDITORIAL DÉTAILLÉ") === ---
    def _get_summary_prompt(self, file_path: str, source_code: str) -> str:
        """
        Génère le prompt SOTA "Éditorial Détaillé" (exhaustif)
        avec troncature adaptative (Fondation Solide).
        """
        
        source_lines = source_code.splitlines()
        line_count = len(source_lines)
        
        if line_count <= SUMMARY_LINE_LIMIT:
            code_to_send = source_code
            prompt_label = "Code à analyser (complet) :"
        else:
            truncated_lines = source_lines[:SUMMARY_LINE_LIMIT]
            code_to_send = "\n".join(truncated_lines)
            prompt_label = f"Code à analyser (tronqué aux {SUMMARY_LINE_LIMIT} premières lignes) :"

        
        return f"""
        Tâche : Analyser le code source fourni ci-dessous et générer un **éditorial technique détaillé**.

        Instructions de Rédaction :
        - Rédiger un résumé structuré avec deux sections principales : "Rôle principal" et "Structure Technique Détaillée".
        - Ne PAS inclure de phrases de confirmation (ex: "Voici le résumé", "J'ai suivi les consignes").
        - Ne PAS répéter ces instructions dans la réponse finale.

        1. Section "Rôle principal" (1-2 phrases) :
           Décrire l'objectif technique et spécifique de ce fichier.
           Exemples : "Définit le service d'authentification", "Configure le build de production".

        2. Section "Structure Technique Détaillée" (Liste) :
           **Lister et décrire TOUTES les sections logiques ou techniques majeures** trouvées dans le code.
           - Code : Lister les classes principales, les groupes de méthodes, la gestion d'état, etc.
           - Config : Lister les sections principales (ex: 'entry', 'output', 'plugins', 'rules', 'devServer').

        Rappel : La réponse doit être basée EXCLUSIVEMENT sur le code fourni, sans généralités sur les technologies.

        {prompt_label}
        ```
        // Fichier : {file_path}
        {code_to_send}
        ```
        
        Éditorial Technique :
        """
    # --- === FIN DE LA MODIFICATION === ---

    def _create_normalized_sheet(self, file_path: str, summary: str, nodes: List[Dict]) -> str:
        sheet = []
        sheet.append(f"# Fiche : {file_path}")
        sheet.append(f"## Résumé Fonctionnel")
        # Nettoyage au cas où le LLM ajouterait "Éditorial Technique :"
        clean_summary = summary.replace("Éditorial Technique :", "").strip()
        sheet.append(clean_summary)
        
        classes = [n for n in nodes if n['type'] == 'Class']
        functions = [n for n in nodes if n['type'] == 'Function']
        interfaces = [n for n in nodes if n['type'] == 'Interface']
        if classes:
            sheet.append(f"\n## Classes ({len(classes)})")
            for c in classes: sheet.append(f"- **{c['name']}**: {c.get('members', 0)} membres")
        if functions:
            sheet.append(f"\n## Fonctions ({len(functions)})")
            for f in functions: sheet.append(f"- **{f['name']}**: {f.get('parameters', 0)} paramètres")
        if interfaces:
            sheet.append(f"\n## Interfaces ({len(interfaces)})")
            for i in interfaces: sheet.append(f"- **{i['name']}**: {i.get('properties', 0)} propriétés")
        return "\n".join(sheet)

    def run_enrichment_pipeline(self, resume: bool = False):
        """
        Point d'entrée principal de l'ETL fiches.

        resume=False (defaut) : rebuild propre (collection recréée).
        resume=True : conserve la collection, SKIP les fichiers déjà indexés, et
        reprend là où un run précédent s'est arrêté. Upload INCRÉMENTAL par lot dans
        les deux cas -> un crash à 90% ne perd pas les 90% déjà faits.
        """
        log.info(f"--- DÉBUT DU PIPELINE D'ENRICHISSEMENT (resume={resume}) ---")

        nodes_by_file = self._load_kg_data()
        
        files_to_process: List[Tuple[str, List[Dict], str]] = []
        skipped_count = 0

        # --- ÉTAPE 1 : Lecture des fichiers ---
        log.info("Phase 1 : Lecture de tous les fichiers sources...")
        for relative_path, nodes in nodes_by_file.items():
            
            absolute_path = PROJECT_ROOT / relative_path
            
            if not absolute_path.exists():
                log.warning(f"Fichier source introuvable (ignoré) : {absolute_path}")
                skipped_count += 1
                continue
            
            try:
                # C'est ici que nous lisons le code COMPLET
                source_code = absolute_path.read_text(encoding='utf-8')
                files_to_process.append((relative_path, nodes, source_code))
            except Exception as e:
                log.error(f"Échec de lecture de {relative_path}: {e}")
                skipped_count += 1
        
        if not files_to_process:
            log.warning("Aucun fichier source valide n'a été trouvé. Arrêt.")
            return

        # --- Collection Qdrant : rebuild (fresh) ou conservée (resume) ---
        self.setup_qdrant_collection(fresh=not resume)

        # --- Reprise : retirer les fichiers déjà indexés ---
        if resume:
            done = self._existing_relative_paths()
            before = len(files_to_process)
            files_to_process = [f for f in files_to_process if f[0] not in done]
            log.info(f"Reprise : {len(done)} fiches déjà présentes, "
                     f"{before - len(files_to_process)} fichiers ignorés, "
                     f"{len(files_to_process)} restants.")
            if not files_to_process:
                log.info("Reprise : tout est déjà indexé. Rien à faire.")
                return

        total_files = len(files_to_process)
        log.info(f"Phase 1 terminée. {total_files} fichiers prêts pour l'enrichissement.")

        audit_outputs = []

        # --- ÉTAPE 2, 3, 4 : Boucle de Mini-Batching SOTA ---
        log.info(f"Démarrage des Phases 2, 3 (Mini-Lots de {MINI_BATCH_SIZE})...")
        
        for i in range(0, total_files, MINI_BATCH_SIZE):
            batch_files = files_to_process[i : i + MINI_BATCH_SIZE]
            batch_start = i + 1
            batch_end = min(i + MINI_BATCH_SIZE, total_files)
            
            log.info(f"\n--- Traitement du Lot {batch_start}-{batch_end} / {total_files} ---")

            # --- Phase 2 : Résumés LLM (boucle PAR FICHIER, instrumentée) ---
            # On n'utilise PLUS llm.batch (opaque + bloque TOUT si une generation
            # reste a 0%). Boucle explicite : timeout par requete (cf. get_chat)
            # + log detaille AVANT/APRES chaque fichier pour localiser tout blocage,
            # + fallback par fichier (un fichier KO ne tue pas le run).
            import time as _time
            summaries = []
            for k, (rel_path, nodes, code) in enumerate(batch_files):
                gidx = batch_start + k
                prompt = self._get_summary_prompt(rel_path, code)
                n_lines = code.count("\n") + 1
                log.info(f"  [LLM {gidx}/{total_files}] -> {rel_path} "
                         f"({n_lines} lignes, prompt {len(prompt)} car.) envoi...")
                t0 = _time.time()
                try:
                    res = self.llm.invoke([HumanMessage(content=prompt)])
                    dt = _time.time() - t0
                    summary = res.content or ""
                    log.info(f"  [LLM {gidx}/{total_files}] OK en {dt:.1f}s "
                             f"(reponse {len(summary)} car.)")
                except Exception as e:
                    dt = _time.time() - t0
                    log.error(f"  [LLM {gidx}/{total_files}] ECHEC apres {dt:.1f}s sur "
                              f"{rel_path}: {type(e).__name__}: {e}")
                    summary = "Résumé non disponible (timeout ou erreur LLM)."
                summaries.append(summary)
            log.info(f"Lot {batch_start}-{batch_end}: Résumés LLM terminés.")

            # --- Phase 3 : Création Fiches & Batch Embeddings ---
            log.debug(f"Lot {batch_start}-{batch_end}: Création des fiches et génération de {len(batch_files)} embeddings...")
            
            fiches_md = [
                self._create_normalized_sheet(batch_files[j][0], summaries[j], batch_files[j][1])
                for j in range(len(batch_files))
            ]
            
            try:
                vectors = self.embedder.embed_documents(fiches_md)
                log.info(f"Lot {batch_start}-{batch_end}: Embeddings terminés.")
            except Exception as e:
                log.critical(f"Échec fatal du batch Embedding pour le lot {batch_start}-{batch_end}: {e}")
                log.error("Ce lot sera ignoré.")
                continue

            # --- Phase 4 : Préparation + UPLOAD INCRÉMENTAL du lot ---
            batch_points = []
            for j in range(len(batch_files)):
                relative_path, nodes, source_code = batch_files[j]

                payload = {
                    "relative_path": relative_path,
                    "summary": summaries[j].replace("Éditorial Technique :", "").strip(), # Nettoyage
                    "fiche_md": fiches_md[j],
                    "main_classes": [n['name'] for n in nodes if n['type'] == 'Class'],
                    "main_functions": [n['name'] for n in nodes if n['type'] == 'Function']
                }

                point_id = str(uuid.uuid4())
                batch_points.append(
                    models.PointStruct(id=point_id, vector=vectors[j], payload=payload)
                )
                audit_outputs.append({
                    "id": point_id,
                    "vector_preview": vectors[j][:5] + ["..."],
                    "payload": payload
                })

            # Upload du lot IMMEDIATEMENT (resilience : un crash ulterieur garde ce lot).
            try:
                self.client.upsert(collection_name=self.collection_name,
                                   points=batch_points, wait=True)
                log.info(f"Lot {batch_start}-{batch_end}: {len(batch_points)} fiches uploadées (incrémental).")
            except Exception as e:
                log.error(f"Échec upload du lot {batch_start}-{batch_end} (ignoré, reprise possible) : {e}")
        
        # --- ÉTAPE 5 : Écriture du fichier d'Audit ---
        log.info(f"Phase 5 : Écriture du fichier d'audit : {AUDIT_JSON_PATH}")
        try:
            with open(AUDIT_JSON_PATH, 'w', encoding='utf-8') as f:
                json.dump(audit_outputs, f, indent=2, ensure_ascii=False)
            log.info(f"✅ Fichier d'audit '{AUDIT_JSON_PATH}' généré avec {len(audit_outputs)} points.")
        except Exception as e:
            log.error(f"Échec de l'écriture du fichier d'audit : {e}")

        # (Upload désormais INCRÉMENTAL par lot ci-dessus ; plus d'upload final global.)

        # --- ÉTAPE 7 : Résumé final ---
        count = self.client.count(collection_name=self.collection_name, exact=True)
        log.info("--- FIN DU PIPELINE D'ENRICHISSEMENT (Fondation Solide) ---")
        log.info(f"✅ {count.count} fiches de métadonnées enrichies indexées dans Qdrant.")
        log.info(f"📊 Statistiques : {total_files - skipped_count} traités, {skipped_count} ignorés")
        log.info(f"Collection: {self.collection_name}")


if __name__ == "__main__":
    setup_logging()
    
    try:
        enricher = MetadataEnricher()
        enricher.run_enrichment_pipeline()
    except Exception as e:
        log.critical(f"Échec fatal du pipeline d'enrichissement : {e}")
        sys.exit(1)