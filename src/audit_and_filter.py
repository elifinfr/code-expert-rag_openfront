# src/audit_and_filter.py
import os, glob, logging, configparser
from typing import List, Set, Dict
from pathlib import Path
import json # Importé pour le manifest détaillé

# --- Sprint 2, Phase 0 (Lot C) : gate multi-langage ---
# Import robuste : relatif (import via src.audit_and_filter) ou absolu (standalone).
try:
    from .languages import (
        build_coverage, decide, format_matrix, format_alert,
        classify_file, language_support, all_extensions,
    )
except ImportError:  # execution standalone : python src/audit_and_filter.py
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.languages import (
        build_coverage, decide, format_matrix, format_alert,
        classify_file, language_support, all_extensions,
    )

# --- MODIFICATION (POC 121) ---
# Le logger est récupéré au niveau du module
log = logging.getLogger(__name__)
# -----------------------------

class SmartCodeAuditor:
    """Auditeur intelligent de code qui filtre les fichiers pertinents"""
    
    def __init__(self, config_path: str = 'config.ini'):
        self.config = configparser.ConfigParser()
        self.config.read(config_path)
        self.code_dir = self.config['DEFAULT']['CODE_DIRECTORY']
        self.project_root = Path(self.code_dir).parent # Racine pour les chemins relatifs
        
        # Extensions de fichiers pertinents
        self.valid_extensions = {
            # Documentation
            '.md', '.mdx',
            # Configuration importante
            '.json', '.yaml', '.yml',
            # Types
            '.d.ts',
        }
        # Sprint 2 : inclure TOUTES les extensions CODE des langages enregistres
        # (TS/JS/Python...) -> l'inclusion au manifest suit le registre, plus de
        # liste codee en dur biaisee TS/JS.
        self.valid_extensions |= set(all_extensions())
        
        # Répertoires à exclure TOUJOURS
        self.exclude_dirs = {
            'node_modules', 'dist', 'build', '.git', 
            'coverage', '.next', '.nuxt', 'out',
            'venv', '__pycache__', '.cache', 
            'vendor', 'tmp', 'temp'
        }
        
        # Patterns de fichiers à exclure
        self.exclude_patterns = {
            '*.min.js', '*.min.css',  # Fichiers minifiés
            '*.bundle.js', '*.chunk.js',  # Bundles
            '*.test.ts', '*.spec.ts',  # Tests (optionnel)
            '*.lock', 'package-lock.json', 'yarn.lock',  # Lock files
            '.DS_Store', 'Thumbs.db',  # Système
        }
        
        # Fichiers de config à garder (importantes pour comprendre le projet)
        self.important_config_files = {
            'package.json', 'tsconfig.json', 'webpack.config.js',
            'vite.config.ts', 'next.config.js', 'README.md'
        }

        # Sprint 2 (Lot C) : TOUS les chemins relatifs scannes (post exclude_dirs),
        # valides ou non -> sert a la matrice de couverture du gate.
        self.all_seen_rel: List[str] = []
    
    def is_valid_file(self, filepath: Path) -> bool:
        """Détermine si un fichier doit être indexé"""
        
        # Cas spécial pour les fichiers importants (ex: webpack.config.js)
        if filepath.name in self.important_config_files:
             try:
                # On vérifie juste qu'il n'est pas trop gros
                return filepath.stat().st_size < 1_000_000 # 1MB
             except:
                return False

        # Vérifier l'extension
        if filepath.suffix not in self.valid_extensions:
            return False
        
        # Vérifier les patterns d'exclusion
        for pattern in self.exclude_patterns:
            if filepath.match(pattern):
                return False
        
        # Vérifier la taille (éviter les fichiers générés trop gros)
        try:
            size = filepath.stat().st_size
            if size > 500_000:  # 500KB max
                log.debug(f"Fichier trop gros ignoré: {filepath.name} ({size/1024:.1f}KB)")
                return False
            if size == 0:  # Fichiers vides
                return False
        except:
            return False
        
        return True
    
    def is_excluded_dir(self, dirpath: Path) -> bool:
        """Vérifie si un répertoire doit être exclu"""
        dir_parts = set(dirpath.parts)
        return bool(dir_parts & self.exclude_dirs)
    
    def analyze_file_importance(self, filepath: Path) -> Dict:
        """Analyse l'importance d'un fichier en lisant son contenu"""
        
        importance_score = 0
        reasons = []
        
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(5000)  # Lire seulement les 5000 premiers caractères
            
            # Critères d'importance
            
            # 1. Exports publics (indique une API publique)
            if 'export ' in content:
                importance_score += 3
                reasons.append("exports publics")
            
            # 2. Documentation (JSDoc, commentaires importants)
            doc_patterns = ['/**', '@param', '@returns', '@example', '# ']
            if any(pattern in content for pattern in doc_patterns):
                importance_score += 2
                reasons.append("documentation")
            
            # 3. Mots-clés importants du domaine
            domain_keywords = [
                'class ', 'interface ', 'type ', 'enum ',  # Structures
                'function ', 'const ', 'let ',  # Définitions
                'import ', 'require(',  # Dépendances
            ]
            keyword_count = sum(1 for kw in domain_keywords if kw in content)
            importance_score += min(keyword_count, 5)
            
            # 4. Taille raisonnable (ni trop petit ni trop grand)
            lines = content.count('\n')
            if 10 < lines < 1000:
                importance_score += 1
            
            # 5. Fichiers de configuration critiques
            if filepath.name in self.important_config_files:
                importance_score += 5
                reasons.append("config critique")
            
            # 6. Fichiers dans des dossiers "core"
            core_dirs = {'src', 'lib', 'core', 'api', 'components', 'utils'}
            if any(part in core_dirs for part in filepath.parts):
                importance_score += 2
                reasons.append("répertoire core")
            
            return {
                'score': importance_score,
                'reasons': reasons,
                'lines': lines
            }
        
        except Exception as e:
            log.debug(f"Erreur analyse {filepath}: {e}")
            return {'score': 0, 'reasons': [], 'lines': 0}
    
    def scan_directory(self) -> List[Dict]:
        """Scanne le répertoire et collecte TOUS les fichiers valides."""
        if not os.path.exists(self.code_dir):
            log.error(f"Répertoire introuvable: {self.code_dir}")
            return []
        
        log.info(f"🔍 Scan du répertoire: {self.code_dir}")
        all_files = []
        excluded_count = 0
        
        for root, dirs, files in os.walk(self.code_dir):
            root_path = Path(root)
            
            # Filtrer les répertoires exclus
            dirs[:] = [d for d in dirs if not self.is_excluded_dir(root_path / d)]
            
            for filename in files:
                filepath = root_path / filename
                rel = str(filepath.relative_to(self.project_root))

                # Gate (Lot C) : on enregistre TOUT fichier vu (post exclude_dirs),
                # meme non valide, pour la matrice de couverture.
                self.all_seen_rel.append(rel)

                if self.is_valid_file(filepath):
                    # Analyser l'importance
                    analysis = self.analyze_file_importance(filepath)
                    status, language = language_support(rel)
                    all_files.append({
                        'path': str(filepath),
                        'relative_path': rel,
                        'score': analysis['score'],
                        'reasons': analysis['reasons'],
                        'lines': analysis['lines'],
                        'size': filepath.stat().st_size,
                        # Enrichissement multi-langage (Lot C)
                        'category': classify_file(rel).value,
                        'language': language,
                        'support_status': status,
                    })
                else:
                    excluded_count += 1
        
        log.info(f"📊 {len(all_files)} fichiers valides trouvés (avant filtrage).")
        log.info(f"🗑️  {excluded_count} fichiers exclus (type, taille, ou dossier).")
        return all_files
    
    def rank_files(self, files: List[Dict]) -> List[Dict]:
        """Trie les fichiers par importance décroissante"""
        return sorted(files, key=lambda x: x['score'], reverse=True)

    def generate_report(self, files: List[Dict]) -> str:
        """Génère un rapport détaillé de l'audit"""
        
        report_lines = [
            "="*80,
            "📋 RAPPORT D'AUDIT DE CODE",
            "="*80,
            f"\n📁 Répertoire analysé: {self.code_dir}",
            f"📊 Nombre total de fichiers: {len(files)}",
            "\n--- STATISTIQUES ---\n"
        ]
        
        # Stats par extension
        extensions = {}
        for f in files:
            ext = Path(f['path']).suffix
            extensions[ext] = extensions.get(ext, 0) + 1
        
        report_lines.append("Extensions trouvées:")
        for ext, count in sorted(extensions.items(), key=lambda x: x[1], reverse=True):
            report_lines.append(f"  {ext}: {count} fichiers")
        
        # Stats par répertoire
        dirs = {}
        for f in files:
            parts = Path(f['relative_path']).parts
            if len(parts) > 1:
                top_dir = parts[0]
                dirs[top_dir] = dirs.get(top_dir, 0) + 1
        
        report_lines.append("\nRépertoires principaux:")
        for dir_name, count in sorted(dirs.items(), key=lambda x: x[1], reverse=True)[:10]:
            report_lines.append(f"  {dir_name}/: {count} fichiers")
        
        # Top fichiers par importance
        report_lines.append("\n--- TOP 20 FICHIERS IMPORTANTS ---\n")
        for i, f in enumerate(files[:20], 1):
            rel_path = f['relative_path']
            score = f['score']
            reasons = ', '.join(f['reasons'])
            report_lines.append(f"{i:2d}. [{score:2d}] {rel_path}")
            if reasons:
                report_lines.append(f"     → {reasons}")
        
        report_lines.append("\n" + "="*80)
        
        return "\n".join(report_lines)
    
    def save_manifest(self, files: List[Dict], output_file: str = "project_manifest.txt"):
        """Sauvegarde la liste des fichiers pertinents"""
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for file_info in files:
                f.write(f"{file_info['relative_path']}\n")
        
        log.info(f"💾 Manifest sauvegardé: {output_file}")
    
    def save_detailed_manifest(self, files: List[Dict], output_file: str = "project_manifest_detailed.json"):
        """Sauvegarde un manifest détaillé en JSON"""
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(files, f, indent=2, ensure_ascii=False)
        
        log.info(f"💾 Manifest détaillé sauvegardé: {output_file}")


# --- FONCTION PRINCIPALE MISE À JOUR (POC 122) ---
def run_audit_and_save_manifest(min_score: int = 5, verbose: bool = True,
                                allow_unsupported: bool = False,
                                interactive: bool = False):
    """
    Lance l'audit intelligent, applique le GATE multi-langage, FILTRE par score,
    et sauvegarde les manifestes.

    Args:
        min_score: Le score minimum requis pour qu'un fichier soit indexé.
        verbose: Afficher le rapport détaillé.
        allow_unsupported: override explicite (indexer TS/JS malgré du code non supporté).
        interactive: si True, le gate DEMANDE a l'humain en cas de code non supporté.

    Raises:
        RuntimeError: si le gate refuse l'indexation (code non supporté, pas d'override).
    """

    auditor = SmartCodeAuditor()

    # 1. Scanner (récupère TOUT)
    log.info("🚀 Démarrage de l'audit intelligent...")
    all_files = auditor.scan_directory()

    if not all_files:
        log.error("❌ Aucun fichier trouvé !")
        return

    # 1 bis. GATE multi-langage (Lot C) : matrice TOUJOURS affichee, STOP si
    # code non supporte present (selon decision humaine / override).
    coverage = build_coverage(auditor.all_seen_rel)
    print()
    print(format_matrix(coverage))
    alert = format_alert(coverage)
    if alert:
        print(alert)
    decision = decide(coverage, interactive=interactive, allow_unsupported=allow_unsupported)
    print(f"\n[GATE] {decision.reason}\n")
    if not decision.proceed:
        # Refus AVANT toute ecriture de manifest : pas d'index partiel silencieux.
        raise RuntimeError(decision.reason)

    # 2. Filtrer (Fondation Solide POC 122)
    log.info(f"Application du filtre : 'score >= {min_score}'...")
    # Nous gardons les READMEs (souvent score < 5) car ils sont utiles pour le contexte
    filtered_files = [
        f for f in all_files 
        if f['score'] >= min_score or f['relative_path'].endswith('README.md')
    ]
    log.info(f"Fichiers retenus après filtrage : {len(filtered_files)} / {len(all_files)}")
    
    # 3. Trier
    ranked_files = auditor.rank_files(filtered_files)
    
    # 4. Générer le rapport (sur les fichiers filtrés)
    if verbose:
        report = auditor.generate_report(ranked_files)
        if report:
            for line in report.split('\n'):
                log.info(line)
    
    # 5. Sauvegarder (les fichiers filtrés)
    auditor.save_manifest(ranked_files)
    auditor.save_detailed_manifest(ranked_files)
    
    # 6. Résumé final
    total_size = sum(f['size'] for f in ranked_files) / (1024 * 1024)  # MB
    total_lines = sum(f['lines'] for f in ranked_files)
    
    log.info(f"\n✅ AUDIT TERMINÉ (FILTRÉ)")
    log.info(f"   📄 {len(ranked_files)} fichiers retenus (Score >= {min_score})")
    log.info(f"   📏 ~{total_lines:,} lignes de code")
    log.info(f"   💾 {total_size:.2f} MB au total")
    log.info(f"   📝 Manifest: project_manifest.txt")
    log.info(f"   📊 Détails: project_manifest_detailed.json")
# --- FIN DE LA MODIFICATION ---


def interactive_audit():
    """Mode interactif pour configurer l'audit"""
    
    print("\n" + "="*60)
    print("🔍 AUDIT INTELLIGENT DE CODE - Configuration")
    print("="*60 + "\n")
    
    try:
        percent = float(input("\nPourcentage (0.1-1.0) [défaut: 1.0]: ").strip() or "1.0")
        percent = max(0.1, min(1.0, percent))
    except:
        percent = 1.0
    
    # --- MODIFICATION (POC 122) : Utilise min_score ---
    print(f"\n✓ Configuration: garder les fichiers avec score >= 5\n")
    run_audit_and_save_manifest(min_score=5, verbose=True, interactive=True)
    
    print("\n" + "="*60)
    view_details = input("Voulez-vous voir les détails du manifest ? (o/N): ").strip().lower()
    
    if view_details == 'o':
        with open("project_manifest_detailed.json", 'r') as f:
            data = json.load(f)
        
        print("\n📊 DÉTAILS DES FICHIERS (Top 30):\n")
        for i, file_info in enumerate(data[:30], 1):
            print(f"{i:2d}. {file_info['relative_path']}")
            print(f"    Score: {file_info['score']} | Lignes: {file_info['lines']} | {', '.join(file_info['reasons'])}")
            print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    interactive_audit()