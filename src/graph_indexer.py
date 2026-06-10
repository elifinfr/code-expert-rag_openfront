# src/graph_indexer.py
# Version 6 (POC 63 - ETL SOTA JSON + CWD Fix)
# 1. Remplacement du 'Cypher Script' (POC 59) par 'JSON ETL' (POC 60)
# 2. Correction du 'loader ESM' (POC 58)
# 3. Correction du 'subprocess CWD' (POC 63)

import logging
import configparser
import sys
import subprocess
import shutil  # <-- AJOUT : resolution npx/npx.cmd (Windows)
from pathlib import Path
import json # <-- AJOUT (POC 60)

print(">>> DEBUG: graph_indexer.py - Module en cours d'importation (Version SOTA POC 63 - ETL JSON + CWD Fix)...")

try:
    from neo4j import GraphDatabase, Driver
    print(">>> DEBUG: graph_indexer.py - Import 'neo4j' réussi.")
except ImportError:
    logging.critical("ÉCHEC : 'neo4j' non installé. Veuillez exécuter : pip install neo4j")
    sys.exit(1)

log = logging.getLogger(__name__)

# =============================================================================
# --- 1. CONFIGURATION ET CLASSE DE CONNEXION ---
# =============================================================================

config = configparser.ConfigParser()
config_path = Path('config.ini')
PROJECT_ROOT = config_path.resolve().parent # <-- AJOUT (POC 63)

if not config_path.exists():
    log.critical("config.ini introuvable à la racine. Lancement impossible.")
else:
    config.read(config_path)

defaults = config['DEFAULT']
NEO4J_URI = defaults.get('NEO4J_URI', 'bolt://localhost:7687')
NEO4J_USER = defaults.get('NEO4J_USER', 'neo4j')
NEO4J_PASSWORD = defaults.get('NEO4J_PASSWORD', 'password')

CODE_DIRECTORY_TO_INDEX = defaults.get('CODE_DIRECTORY')
TS_ANALYZER_SCRIPT_PATH = Path('src/graph_builder/ts_kg_analyzer.ts')
CYPHER_SCRIPT_PATH = Path('neo4j-import.cypher') # Gardé pour le nettoyage (POC 60)
JSON_PATH = Path('knowledge-graph.json') # <-- AJOUT (POC 60)

print(">>> DEBUG: graph_indexer.py - Définition de la classe CodeGraphBuilder (SOTA).")

class CodeGraphBuilder:
    """
    Orchestre la connexion et le chargement des données du graphe dans Neo4j.
    """
    driver: Driver = None

    def __init__(self, uri, user, password):
        try:
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            self.driver.verify_connectivity()
            log.info(f"Connexion à Neo4j ({uri}) réussie.")
        except Exception as e:
            log.critical(f"Échec de la connexion à Neo4j: {e}")
            raise

    def close(self):
        if self.driver:
            self.driver.close()
            log.info("Connexion Neo4j fermée.")

    def run_query(self, query: str, **params):
        """Exécute une requête Cypher et retourne les résultats."""
        try:
            with self.driver.session() as session:
                result = session.run(query, **params)
                return [record for record in result]
        except Exception as e:
            log.error(f"Échec de la requête Cypher : {query} | Erreur: {e}", exc_info=True)
            return None

    def _run_transaction(self, tx, query):
        """Helper pour exécuter une requête dans une transaction."""
        tx.run(query)

    def clear_database(self):
        """
        Vide complètement la base de données Neo4j.
        """
        log.warning("--- NETTOYAGE COMPLET DE LA BASE NEO4J ---")
        try:
            with self.driver.session(database="neo4j") as session:
                try:
                    constraints = session.run("SHOW CONSTRAINTS")
                    for record in constraints:
                        constraint_name = record["name"]
                        log.debug(f"Suppression contrainte existante : {constraint_name}")
                        session.run(f"DROP CONSTRAINT {constraint_name}")
                except Exception as e:
                    log.error(f"Échec suppression contraintes (peut être normal si BDD vide): {e}")

                log.info("Suppression de tous les nœuds et relations...")
                session.run("MATCH (n) DETACH DELETE n")
                log.info("Nettoyage de la BDD terminé.")
        except Exception as e:
            log.error(f"Échec lors du nettoyage de la BDD Neo4j : {e}", exc_info=True)
            raise

    # --- SUPPRIMÉ (POC 60) ---
    # def run_cypher_script(self, script_content: str): ...
    # --- FIN SUPPRESSION ---

    # --- AJOUT (POC 60) : Méthode SOTA 1 - Contraintes ---
    def create_constraints(self, node_types: set):
        """
        Applique les contraintes d'unicité sur l'ID pour tous les types de nœuds.
        C'est une fondation solide indispensable pour le MATCHing des arêtes.
        """
        log.info(f"Application de {len(node_types)} contraintes d'unicité (fondation solide)...")
        try:
            with self.driver.session(database="neo4j") as session:
                for node_type in node_types:
                    log.debug(f"Application contrainte sur : {node_type}")
                    session.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{node_type}) REQUIRE n.id IS UNIQUE;")
            log.info("Contraintes appliquées.")
        except Exception as e:
            log.error(f"Échec lors de la création des contraintes: {e}", exc_info=True)
            raise

    # --- AJOUT (POC 60) : Méthode SOTA 2 - Chargement Nœuds ---
    def batch_load_nodes(self, nodes: list):
        """
        Charge les nœuds par batch en utilisant UNWIND et des requêtes paramétrées.
        Regroupe par type de nœud car le LABEL ne peut pas être paramétré.
        """
        log.info(f"Chargement de {len(nodes)} nœuds (batch SOTA)...")
        
        # 1. Regrouper les nœuds par type
        nodes_by_type = {}
        for node_props in nodes:
            node_type = node_props.pop('type') # Retirer 'type' des props
            if node_type not in nodes_by_type:
                nodes_by_type[node_type] = []
            nodes_by_type[node_type].append(node_props) 
        
        # 2. Template de requête SOTA (paramétré)
        query_template = "UNWIND $batch AS props CREATE (n:{type}) SET n = props"
        
        try:
            with self.driver.session(database="neo4j") as session:
                for node_type, batch in nodes_by_type.items():
                    log.debug(f"Chargement de {len(batch)} nœuds de type {node_type}")
                    session.run(query_template.format(type=node_type), batch=batch)
            log.info("Chargement des nœuds terminé.")
        except Exception as e:
            log.error(f"Échec lors du batch load des nœuds: {e}", exc_info=True)
            raise

    # --- AJOUT (POC 60) : Méthode SOTA 3 - Chargement Arêtes ---
    def batch_load_edges(self, edges: list):
        """
        Charge les arêtes par batch. Utilise MATCH sur les ID (contraintes)
        et regroupe par type de relation.
        """
        log.info(f"Chargement de {len(edges)} arêtes (batch SOTA)...")

        # 1. Regrouper les arêtes par type
        edges_by_type = {}
        for edge_props in edges:
            edge_type = edge_props.pop('type') # Retirer 'type' des props
            if edge_type not in edges_by_type:
                edges_by_type[edge_type] = []
            edges_by_type[edge_type].append(edge_props)
        
        # 2. Template de requête SOTA (paramétré)
        # On ne peut pas paramétrer le type de relation (ex: [r:$type])
        # Nous devons formater le type dans la string (sécurisé, vient de notre enum)
        query_template = """
        UNWIND $batch_props AS props
        MATCH (a {id: props.source})
        MATCH (b {id: props.target})
        CREATE (a)-[r:%s]->(b)
        SET r = props
        """
        
        try:
            with self.driver.session(database="neo4j") as session:
                for edge_type, batch in edges_by_type.items():
                    log.debug(f"Chargement de {len(batch)} arêtes de type {edge_type}")
                    
                    # Formate le type de relation dans la requête
                    final_query = query_template % edge_type
                    
                    session.run(final_query, batch_props=batch)
            log.info("Chargement des arêtes terminé.")
        except Exception as e:
            log.error(f"Échec lors du batch load des arêtes: {e}", exc_info=True)
            raise
    # --- FIN AJOUT (POC 60) ---

# =============================================================================
# --- 2. POINT D'ENTRÉE POUR MAIN.PY (MODIFIÉ POC 60 & 63) ---
# =============================================================================

print(">>> DEBUG: graph_indexer.py - Définition de la fonction build_knowledge_graph (SOTA ETL JSON + CWD Fix).")

def _build_with_tsmorph():
    """[ROLLBACK] Ancien pipeline ts-morph (Node.js -> JSON -> Neo4j).

    Conserve pour rollback tant que le backbone SCIP n'est pas definitivement adopte.
    Active via config.ini : GRAPH_BACKEND = tsmorph
    """
    log.info("--- DÉBUT Construction Knowledge Graph (Pipeline ETL SOTA) ---")
    
    # --- VALIDATION DES FONDATIONS ---
    if not NEO4J_URI:
        log.critical("Configuration Neo4j (NEO4J_URI) non trouvée dans config.ini.")
        return
    if not CODE_DIRECTORY_TO_INDEX:
        log.critical("CODE_DIRECTORY non trouvé dans config.ini.")
        return
    if not TS_ANALYZER_SCRIPT_PATH.exists():
        log.critical(f"Script analyseur SOTA introuvable: {TS_ANALYZER_SCRIPT_PATH}")
        log.critical("Assurez-vous que 'src/graph_builder/ts_kg_analyzer.ts' (POC 60) existe.")
        return

    # --- SUPPRIMÉ (POC 61 & 63) : Logique du chemin absolu ---
    
    # --- PHASE 1: EXTRACTION (Appel Node.js) ---
    log.info("--- Phase 1: Extraction (Node.js/ts-morph via CWD Fix) ---")
    log.info(f"Lancement de l'analyseur SOTA sur {CODE_DIRECTORY_TO_INDEX}...")
    
    # --- BLOC CORRIGÉ (Fondation Solide POC 122.D) ---
    # Nous utilisons 'tsx', l'exécutable SOTA "zéro-config"
    # (basé sur esbuild) identifié par notre recherche.
    # C'est la fondation stable pour exécuter des scripts TS ESM.

    # FIX Windows : subprocess ne resout pas "npx" -> "npx.cmd" sans shell.
    # On resout l'executable reel via shutil.which.
    npx_exe = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    node_command = [
        npx_exe,
        "tsx",
        str(TS_ANALYZER_SCRIPT_PATH), # 'src/graph_builder/ts_kg_analyzer.ts'
        CODE_DIRECTORY_TO_INDEX      # './OpenFrontIO' (depuis config.ini)
    ]
    # --- FIN BLOC CORRIGÉ ---
    
    try:
        # --- AJOUT (POC 63) : Spécification CWD (Fondation Solide) ---
        subprocess.run(
            node_command,
            check=True,
            capture_output=True,
            text=True,
            encoding='utf-8',
            cwd=PROJECT_ROOT # <-- FORCER LE CWD
        )
        # --- FIN AJOUT (POC 63) ---
        
        log.info("Extraction Node.js terminée avec succès.")
        log.info(f"Fichier '{JSON_PATH}' généré (ou mis à jour).")
    
    except FileNotFoundError:
        log.critical("ERREUR: 'node' n'est pas trouvé.")
        log.critical("Assurez-vous que Node.js/NPM est installé et dans votre PATH.")
        return
    except subprocess.CalledProcessError as e:
        log.error("--- ÉCHEC DE LA PHASE 1 (EXTRACTION NODE.JS) ---")
        log.error(f"La commande Node.js a échoué avec le code {e.returncode}.")
        log.error("Sortie (stdout) de Node.js:")
        log.error(e.stdout)
        log.error("Erreur (stderr) de Node.js:")
        log.error(e.stderr)
        log.error("Le chargement Neo4j est annulé.")
        return
    except Exception as e:
        log.error(f"Une erreur inattendue est survenue lors de la Phase 1: {e}", exc_info=True)
        return

    # --- PHASE 2: CHARGEMENT (Python/Neo4j via JSON SOTA) (MODIFIÉ POC 60) ---
    log.info("--- Phase 2: Chargement (Python/Neo4j via JSON SOTA) ---")
    
    if not JSON_PATH.exists():
        log.error(f"Fichier d'import '{JSON_PATH}' non trouvé après l'extraction !")
        log.error("Le script 'ts_kg_analyzer.ts' (POC 60) n'a pas généré le fichier.")
        return

    builder = None
    try:
        # 1. Lire le JSON
        log.info(f"Lecture du KG depuis {JSON_PATH}...")
        with open(JSON_PATH, 'r', encoding='utf-8') as f:
            kg_data = json.load(f)
        
        nodes = kg_data.get('nodes', [])
        edges = kg_data.get('edges', [])
        
        if not nodes:
            log.error("Le JSON ne contient aucun nœud. L'analyse a peut-être échoué.")
            return

        # 2. Initialiser le constructeur (et se connecter)
        builder = CodeGraphBuilder(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
        
        # 3. Nettoyer la BDD (Fondation Solide)
        builder.clear_database()
        
        # 4. Créer les contraintes (Fondation Solide)
        node_types = {n['type'] for n in nodes} # Récupère les types uniques
        builder.create_constraints(node_types)
        
        # 5. Exécuter le chargement par batch (SOTA)
        builder.batch_load_nodes(nodes)
        builder.batch_load_edges(edges)
        
        log.info("--- FIN Construction Knowledge Graph (ETL SOTA) ---")
        log.info("Le Knowledge Graph a été importé avec succès dans Neo4j.")

    except Exception as e:
        log.error(f"Échec de la 'Phase 2 (Chargement)': {e}", exc_info=True)
    finally:
        if builder:
            builder.close()
            
        # Nettoyage des fichiers intermédiaires
        if JSON_PATH.exists():
            log.debug(f"Presence du du JSON intermediaire (non supprime) : {JSON_PATH}")

        if CYPHER_SCRIPT_PATH.exists():
             log.debug(f"Presence de l'ancien fichier Cypher (non supprime) : {CYPHER_SCRIPT_PATH}")

    # --- FIN MODIFICATION (POC 60) ---

# =============================================================================
# --- 3. BACKBONE SCIP (Sprint 2, Phase 0 - Lot D) ---
# =============================================================================
GRAPH_BACKEND = defaults.get('GRAPH_BACKEND', 'scip').strip().lower()
MANIFEST_JSON = Path('project_manifest_detailed.json')


def _build_with_scip():
    """Pipeline graphe SCIP-in-Docker -> loader unique -> Neo4j.

    1. scip-typescript (Docker) sur le projet -> index.scip
    2. scip_loader : index.scip -> nodes/edges (filtre manifest, mapping unique)
    3. CodeGraphBuilder : chargement Neo4j (maillon inchange)
    """
    import json as _json
    from collections import Counter
    from .graph_builder.scip_runner import run_scip_for_language, SCIP_OUT_DIRNAME
    from .graph_builder.scip_loader import build_graph_from_scip

    log.info("--- DÉBUT Construction Knowledge Graph (Backbone SCIP/Docker) ---")

    if not NEO4J_URI or not CODE_DIRECTORY_TO_INDEX:
        log.critical("Config Neo4j/CODE_DIRECTORY manquante.")
        return
    if not MANIFEST_JSON.exists():
        log.critical(f"Manifest introuvable : {MANIFEST_JSON}. Lancer l'audit d'abord.")
        return

    code_dir = (PROJECT_ROOT / CODE_DIRECTORY_TO_INDEX).resolve()
    project_name = code_dir.name
    out_dir = PROJECT_ROOT / SCIP_OUT_DIRNAME

    manifest = _json.loads(MANIFEST_JSON.read_text(encoding='utf-8'))
    rels = {fi['relative_path'].replace('\\', '/') for fi in manifest}

    # Langage primaire VALIDATED du projet -> indexeur SCIP correspondant.
    langs = Counter(fi.get('language') for fi in manifest
                    if fi.get('support_status') == 'VALIDATED' and fi.get('language'))
    if not langs:
        log.error("Aucun langage VALIDATED dans le manifest (gate). Graphe annulé.")
        return
    primary = langs.most_common(1)[0][0]
    log.info(f"Langage primaire : {primary} ({dict(langs)})")

    # --- Phase 1 : indexation SCIP (Docker) ---
    log.info(f"--- Phase 1 : SCIP ({primary}) en Docker ---")
    try:
        index_path = run_scip_for_language(primary, code_dir, out_dir, project_name)
    except Exception as e:
        log.error(f"Échec indexation SCIP : {e}")
        return

    # --- Phase 2 : index.scip -> nodes/edges (filtre manifest) ---
    log.info("--- Phase 2 : loader SCIP -> graphe ---")
    g = build_graph_from_scip(index_path, rels, project_name)
    log.info(f"Graphe SCIP : {g.stats}")

    if not g.nodes:
        log.error("Aucun node produit par le loader SCIP. Chargement annulé.")
        return

    # --- Phase 2 bis : ecrire knowledge-graph.json (consomme par les fiches) ---
    # IMPORTANT : avant batch_load_nodes qui MUTE les dicts (pop 'type').
    # metadata_enricher (fiches) lit ce fichier comme source de verite structurelle ;
    # sans lui, les fiches retomberaient sur l'ancien graphe ts-morph (stale).
    try:
        JSON_PATH.write_text(
            _json.dumps({"nodes": g.nodes, "edges": g.edges}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(f"knowledge-graph.json ecrit ({len(g.nodes)} nodes) pour les fiches.")
    except Exception as e:
        log.error(f"Echec ecriture knowledge-graph.json : {e}")

    # --- Phase 3 : chargement Neo4j (CodeGraphBuilder, inchangé) ---
    log.info("--- Phase 3 : chargement Neo4j ---")
    builder = None
    try:
        builder = CodeGraphBuilder(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
        builder.clear_database()
        node_types = {n['type'] for n in g.nodes}
        builder.create_constraints(node_types)
        builder.batch_load_nodes(g.nodes)   # NB: mute les dicts (pop 'type')
        builder.batch_load_edges(g.edges)
        log.info("--- FIN Construction Knowledge Graph (SCIP) ---")
        log.info(f"Graphe SCIP charge : {g.stats['nodes_total']} nodes / {g.stats['edges_total']} edges.")
    except Exception as e:
        log.error(f"Échec chargement Neo4j (SCIP) : {e}", exc_info=True)
    finally:
        if builder:
            builder.close()


def build_knowledge_graph():
    """Point d'entrée graphe (appelé par rag.py / main.py).

    Backbone selectionne par config.ini GRAPH_BACKEND (defaut 'scip').
    'tsmorph' = rollback vers l'ancien pipeline.
    """
    if GRAPH_BACKEND == 'tsmorph':
        log.info("GRAPH_BACKEND=tsmorph -> pipeline ts-morph (rollback).")
        return _build_with_tsmorph()
    log.info("GRAPH_BACKEND=scip -> backbone SCIP/Docker.")
    return _build_with_scip()


print(">>> DEBUG: graph_indexer.py - Fin du module (SOTA ETL JSON + CWD Fix).")

# --- Point d'entrée standalone (pour débogage) ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO) # Setup logging pour standalone
    log.info("Exécution de graph_indexer.py en mode standalone (ETL)...")
    build_knowledge_graph()