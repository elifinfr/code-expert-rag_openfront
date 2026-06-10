# src/json_indexer.py
#
# OBJECTIF : ETL pour la Brique 1.D (Base de Données "Classique")
# Lit le manifest filtré et indexe TOUS les fichiers .json
# dans une base de données SQLite pour une recherche structurée.

import logging
import sys
import json
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

# Constantes
MANIFEST_FILE = Path("project_manifest_detailed.json")
DB_PATH = Path("json_db.sqlite")
PROJECT_ROOT = Path(__file__).resolve().parent.parent

def setup_logging():
    """Configure le logging pour le mode standalone."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] - %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

def index_json_files():
    """
    Point d'entrée principal pour l'indexation JSON, appelé par main.py.
    """
    log.info("--- DÉBUT DE L'INDEXATION JSON (Brique 1.D - SQLite) ---")
    
    if not MANIFEST_FILE.exists():
        log.critical(f"ERREUR : Manifest introuvable : {MANIFEST_FILE}")
        log.critical("Veuillez d'abord exécuter l'Option 1 (Audit) depuis main.py")
        raise FileNotFoundError(f"{MANIFEST_FILE} manquant.")
        
    log.info(f"Lecture du manifest : {MANIFEST_FILE}")
    with open(MANIFEST_FILE, 'r', encoding='utf-8') as f:
        manifest_data: List[Dict] = json.load(f)

    # Filtrer pour ne garder que les .json
    json_files_info = [
        f for f in manifest_data 
        if f['relative_path'].endswith('.json')
    ]
    
    if not json_files_info:
        log.warning("Aucun fichier .json pertinent (score >= 5) trouvé dans le manifest. Indexation JSON terminée.")
        return

    log.info(f"Trouvé {len(json_files_info)} fichiers .json à indexer.")
    
    # --- Initialisation de la BDD SQLite ---
    try:
        if DB_PATH.exists():
            log.warning(f"Nettoyage de l'ancienne base de données : {DB_PATH}")
            DB_PATH.unlink()
            
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Création de la table (Fondation Solide)
        cursor.execute("""
        CREATE TABLE json_files (
            relative_path TEXT PRIMARY KEY,
            content TEXT,
            score INTEGER,
            size_kb REAL
        );
        """)
        log.info(f"Base de données SQLite créée : {DB_PATH}")

        # --- Boucle d'indexation ---
        count = 0
        for file_info in json_files_info:
            try:
                relative_path = file_info['relative_path']
                absolute_path = PROJECT_ROOT / relative_path
                
                content = absolute_path.read_text(encoding='utf-8')
                
                # Valider que c'est du JSON valide
                json.loads(content) 
                
                cursor.execute(
                    "INSERT INTO json_files (relative_path, content, score, size_kb) VALUES (?, ?, ?, ?)",
                    (
                        relative_path,
                        content,
                        file_info['score'],
                        file_info['size'] / 1024.0
                    )
                )
                count += 1
            except json.JSONDecodeError:
                log.warning(f"Fichier .json invalide (ignoré) : {relative_path}")
            except Exception as e:
                log.error(f"Échec lors de l'indexation de {relative_path}: {e}")
        
        conn.commit()
        
    except sqlite3.Error as e:
        log.critical(f"Erreur fatale SQLite : {e}")
    finally:
        if conn:
            conn.close()
            
    log.info("--- FIN DE L'INDEXATION JSON ---")
    log.info(f"✅ {count} fichiers .json indexés dans {DB_PATH}.")

if __name__ == "__main__":
    setup_logging()
    log.info("Démarrage du module 'json_indexer' en mode standalone...")
    index_json_files()