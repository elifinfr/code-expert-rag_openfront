// src/graph_builder/ts_kg_analyzer.ts
//
// VERSION SOTA (POC 122.E - FIX CRITIQUE)
// - CORRECTION: Utilisation de path.dirname() au lieu de .parent
// - Lit 'project_manifest_detailed.json' (filtré par Python).
// - Parse TS, TSX, JS, JSX, MJS avec ts-morph.
// - IGNORE les .json (dédiés à la BDD SQLite).
// - Crée des nœuds pour tous les fichiers pertinents.

import { Project, SyntaxKind, ClassDeclaration, FunctionDeclaration, InterfaceDeclaration, MethodDeclaration, CallExpression, SourceFile } from 'ts-morph';
import * as fs from 'fs';
import * as path from 'path';

// Types pour le Knowledge Graph
interface Node {
  id: string;
  type: 'Class' | 'Interface' | 'Function' | 'Method' | 'File' | 'Config';
  name: string;
  filePath: string; // Doit être le chemin RELATIF
  line: number;
  properties?: Record<string, any>;
}

interface Edge {
  id: string;
  source: string;
  target: string;
  type: 'HERITE_DE' | 'IMPLEMENTE' | 'APPELLE' | 'DEFINIT_DANS' | 'IMPORTE';
  properties?: Record<string, any>;
}

interface KnowledgeGraph {
  nodes: Node[];
  edges: Edge[];
}

// Interface pour le Manifest généré par Python
interface ManifestFile {
    path: string; // Chemin absolu (utilisé pour lire le fichier)
    relative_path: string; // Chemin relatif (utilisé comme ID)
    score: number;
    reasons: string[];
    lines: number;
    size: number;
}

class TypeScriptKGAnalyzer {
  private project: Project;
  private nodes: Map<string, Node> = new Map();
  private edges: Edge[] = [];
  private edgeIdCounter = 0;
  // Racine du projet (ex: /mnt/d/OPENFRONT/code-expert-rag)
  private projectBasePath: string;
  // Racine d'execution (CWD = code-expert-rag) : base des chemins canoniques.
  private projectRoot: string = process.cwd();

  constructor(projectPath: string, tsConfigPath?: string) {
    // ✅ CORRECTION CRITIQUE (POC 122.F)
    // Le projectBasePath DOIT être le répertoire CODE_DIRECTORY lui-même,
    // pas son parent, pour que path.relative() génère les bons chemins
    this.projectBasePath = path.resolve(projectPath);
    
    console.log(`[DEBUG] projectPath argument: ${projectPath}`);
    console.log(`[DEBUG] projectBasePath (résolu): ${this.projectBasePath}`);
    
    // Tolerant : si pas de tsconfig.json (projet quelconque), on cree un Project
    // generique avec allowJs. Sinon on utilise le tsconfig du projet.
    const tsConfigFilePath = tsConfigPath || path.join(projectPath, 'tsconfig.json');
    if (fs.existsSync(tsConfigFilePath)) {
      console.log(`Utilisation du tsconfig: ${tsConfigFilePath}`);
      this.project = new Project({
        tsConfigFilePath: tsConfigFilePath,
        skipAddingFilesFromTsConfig: true
      });
    } else {
      console.log(`Aucun tsconfig.json trouve -> Project generique (allowJs).`);
      this.project = new Project({
        compilerOptions: { allowJs: true }
      });
    }
  }

  /**
   * Génère un ID de nœud stable basé sur son type, nom, et chemin RELATIF.
   */
  /**
   * Chemin canonique : relatif a la racine d'execution (code-expert-rag),
   * en slashs posix. Garantit la coherence File <-> entites (Class/Function...).
   */
  private toCanonical(absPath: string): string {
    return path.relative(this.projectRoot, absPath).split(path.sep).join('/');
  }

  private generateNodeId(type: string, name: string, relativeFilePath: string): string {
    return `${type}::${name}::${relativeFilePath}`;
  }

  private generateEdgeId(): string { 
    return `e_${this.edgeIdCounter++}`; 
  }
  
  private addNode(node: Node): void { 
    if (!this.nodes.has(node.id)) { 
      this.nodes.set(node.id, node); 
    } 
  }
  
  private addEdge(edge: Edge): void { 
    this.edges.push(edge); 
  }

  /**
   * Crée un nœud ou récupère le nœud existant.
   * Utilise le CHEMIN RELATIF comme identifiant de fichier.
   */
  private getOrCreateNode( type: Node['type'], name: string, relativeFilePath: string, line: number, properties?: Record<string, any>): Node {
    const id = this.generateNodeId(type, name, relativeFilePath);
    const existingNode = this.nodes.get(id);
    if (existingNode) { 
      return existingNode; 
    }
    
    const node: Node = { id, type, name, filePath: relativeFilePath, line, properties };
    this.addNode(node);
    return node;
  }

  public analyze(manifestFiles: ManifestFile[]): KnowledgeGraph {
    console.log('🔍 Début de l\'analyse du projet (pilotée par Manifest SOTA)...\n');

    const parsableExtensions = ['.ts', '.tsx', '.js', '.jsx', '.mjs'];
    const sourceFiles: SourceFile[] = [];

    // 1. Créer un nœud :File pour CHAQUE fichier et ajouter les fichiers parsables au projet
    for (const file of manifestFiles) {
        const ext = path.extname(file.path);
        
        // Exclure JSON (géré par Brique 1.D)
        if (ext === '.json') {
            console.log(`(Graphe) JSON ignoré (géré par BDD SQLite): ${file.relative_path}`);
            continue;
        }
        
        // Ajouter les fichiers parsables au projet ts-morph
        if (parsableExtensions.includes(ext)) {
            try {
                const sf = this.project.addSourceFileAtPath(file.path);
                sourceFiles.push(sf);
            } catch (e: any) {
                console.warn(`⚠️ ts-morph a ignoré ${file.relative_path}: ${e.message}`);
            }
        }
        
// --- CORRECTION CRITIQUE (POC 123.C) ---
        // Ne PAS copier les 'fileProps'. Ne créer que le nœud de base.
        
        const fileType = (ext === '.ts' || ext === '.tsx' || ext === '.js') ? 'File' : 'Config';
        const canonicalRel = file.relative_path.split(/[\\/]/).join('/');

        this.getOrCreateNode(
            fileType,
            path.basename(canonicalRel),
            canonicalRel,
            0,
            // Propriétés minimales. Pas de 'score', 'reasons', etc.
            { size: file.size, lines: file.lines }
        );
        // --- FIN CORRECTION ---
    }
    
    console.log(`📁 ${sourceFiles.length} fichiers parsables ajoutés au projet ts-morph.`);
    console.log(`(Total de ${this.nodes.size} nœuds Fichier/Config créés)`);

    // 3. Analyser les fichiers parsés (TS, JS)
    for (const sourceFile of sourceFiles) {
      const absolutePath = sourceFile.getFilePath();
      const relativePath = this.toCanonical(absolutePath);

      console.log(`📄 Analyse AST de ${relativePath}...`);
      this.analyzeClasses(sourceFile.getClasses(), relativePath);
      this.analyzeInterfaces(sourceFile.getInterfaces(), relativePath);
      this.analyzeFunctions(sourceFile.getFunctions(), relativePath);
    }

    console.log(`\n✅ Analyse terminée !`);
    console.log(`📊 Statistiques :`);
    console.log(`   - Nœuds : ${this.nodes.size}`);
    console.log(`   - Arêtes : ${this.edges.length}`);

    return {
      nodes: Array.from(this.nodes.values()),
      edges: this.edges
    };
  }

  // --- Fonctions d'analyse (Modifiées pour utiliser relativePath) ---

  private analyzeClasses(classes: ClassDeclaration[], relativePath: string): void {
    for (const classDecl of classes) {
      const className = classDecl.getName();
      if (!className) continue;

      const line = classDecl.getStartLineNumber();
      const classNode = this.getOrCreateNode('Class', className, relativePath, line, {
        isAbstract: classDecl.isAbstract(),
        isExported: classDecl.isExported(),
        members: classDecl.getMembers().length
      });

      // Analyser l'héritage (HERITE_DE)
      const baseClass = classDecl.getBaseClass();
      if (baseClass) {
        const baseClassName = baseClass.getName();
        if (baseClassName) {
          const baseNode = this.getOrCreateNode(
            'Class',
            baseClassName,
            this.getRelativePathForDeclaration(baseClass),
            baseClass.getStartLineNumber()
          );

          this.addEdge({
            id: this.generateEdgeId(),
            source: classNode.id,
            target: baseNode.id,
            type: 'HERITE_DE',
          });
        }
      }

      // Analyser les implémentations (IMPLEMENTE)
      const implementedInterfaces = classDecl.getImplements();
      for (const impl of implementedInterfaces) {
        const interfaceName = impl.getText();
        let interfaceNode: Node;
        const interfaceSymbol = impl.getType().getSymbol();
        
        if (interfaceSymbol && interfaceSymbol.getDeclarations().length > 0) {
            const decl = interfaceSymbol.getDeclarations()[0];
            interfaceNode = this.getOrCreateNode(
                'Interface',
                interfaceName,
                this.getRelativePathForDeclaration(decl),
                decl.getStartLineNumber()
            );
        } else {
            interfaceNode = this.getOrCreateNode(
                'Interface',
                interfaceName,
                relativePath,
                impl.getStartLineNumber()
            );
        }

        this.addEdge({
          id: this.generateEdgeId(),
          source: classNode.id,
          target: interfaceNode.id,
          type: 'IMPLEMENTE',
        });
      }

      // Analyser les méthodes de la classe
      this.analyzeMethods(classDecl.getMethods(), classNode, relativePath);

      // Analyser les constructeurs
      const constructors = classDecl.getConstructors();
      for (const constructor of constructors) {
        this.analyzeCallsInFunction(constructor, classNode);
      }
    }
  }

  private analyzeInterfaces(interfaces: InterfaceDeclaration[], relativePath: string): void {
    for (const interfaceDecl of interfaces) {
      const interfaceName = interfaceDecl.getName();
      const line = interfaceDecl.getStartLineNumber();

      const interfaceNode = this.getOrCreateNode('Interface', interfaceName, relativePath, line, {
        isExported: interfaceDecl.isExported(),
        properties: interfaceDecl.getProperties().length
      });

      // Analyser les extensions d'interfaces
      const extendedTypes = interfaceDecl.getExtends();
      for (const extended of extendedTypes) {
        const extendedName = extended.getText();
        let extendedNode: Node;
        const extendedSymbol = extended.getType().getSymbol();
        
        if(extendedSymbol && extendedSymbol.getDeclarations().length > 0) {
            const decl = extendedSymbol.getDeclarations()[0];
             extendedNode = this.getOrCreateNode(
                'Interface',
                extendedName,
                this.getRelativePathForDeclaration(decl),
                decl.getStartLineNumber()
            );
        } else {
             extendedNode = this.getOrCreateNode(
                'Interface',
                extendedName,
                relativePath,
                extended.getStartLineNumber()
            );
        }

        this.addEdge({
          id: this.generateEdgeId(),
          source: interfaceNode.id,
          target: extendedNode.id,
          type: 'HERITE_DE',
        });
      }
    }
  }

  private analyzeFunctions(functions: FunctionDeclaration[], relativePath: string): void {
    for (const funcDecl of functions) {
      const funcName = funcDecl.getName();
      if (!funcName) continue;

      const line = funcDecl.getStartLineNumber();
      const funcNode = this.getOrCreateNode('Function', funcName, relativePath, line, {
        isAsync: funcDecl.isAsync(),
        isExported: funcDecl.isExported(),
        parameters: funcDecl.getParameters().length
      });

      this.analyzeCallsInFunction(funcDecl, funcNode);
    }
  }

  private analyzeMethods(methods: MethodDeclaration[], parentNode: Node, relativePath: string): void {
    for (const method of methods) {
      const methodName = method.getName();
      const line = method.getStartLineNumber();

      const methodNode = this.getOrCreateNode('Method', methodName, relativePath, line, {
        isAsync: method.isAsync(),
        isStatic: method.isStatic(),
        parameters: method.getParameters().length
      });

      // Lien DEFINIT_DANS entre méthode et classe
      this.addEdge({
        id: this.generateEdgeId(),
        source: methodNode.id,
        target: parentNode.id,
        type: 'DEFINIT_DANS',
      });

      this.analyzeCallsInFunction(method, methodNode);
    }
  }
  
  // Fonction pour obtenir le chemin relatif d'une déclaration
  private getRelativePathForDeclaration(declaration: any): string {
    const sourceFile = declaration.getSourceFile();
    if (!sourceFile) return 'unknown.file';
    return this.toCanonical(sourceFile.getFilePath());
  }

  private analyzeCallsInFunction(
    func: FunctionDeclaration | MethodDeclaration | any,
    funcNode: Node
  ): void {
    const callExpressions = func.getDescendantsOfKind(SyntaxKind.CallExpression);

    for (const callExpr of callExpressions) {
      try {
        const calledName = this.extractCalledFunctionName(callExpr);
        if (!calledName) continue;

        const signature = callExpr.getType().getCallSignatures()[0];
        let targetNode: Node;

        if (signature) {
          const declaration = signature.getDeclaration();
          if (declaration) {
            const declSourceFile = declaration.getSourceFile();
            if (declSourceFile.getFilePath().includes('node_modules')) {
                 continue;
            }
              
            const declName = this.getDeclarationName(declaration);
            const declLine = declaration.getStartLineNumber();
            const declType = this.getDeclarationType(declaration);
            const declRelativePath = this.getRelativePathForDeclaration(declaration);

            targetNode = this.getOrCreateNode(
              declType,
              declName || calledName,
              declRelativePath,
              declLine
            );
          } else {
            targetNode = this.getOrCreateNode('Function', calledName, 'external.file', 0, { external: true });
          }
        } else {
          targetNode = this.getOrCreateNode('Function', calledName, 'unresolved.file', 0, { unresolved: true });
        }

        this.addEdge({
          id: this.generateEdgeId(),
          source: funcNode.id,
          target: targetNode.id,
          type: 'APPELLE',
          properties: { callLine: callExpr.getStartLineNumber() }
        });
      } catch (error) {
         // Ignorer les erreurs de résolution
      }
    }
  }

  private extractCalledFunctionName(callExpr: CallExpression): string | null {
    const expression = callExpr.getExpression();
    
    if (expression.getKind() === SyntaxKind.Identifier) {
      return expression.getText();
    }

    if (expression.getKind() === SyntaxKind.PropertyAccessExpression) {
      const propAccess = expression as any;
      return propAccess.getName();
    }
    
    if (expression.getKind() === SyntaxKind.SuperKeyword) {
        return "super";
    }

    return expression.getText();
  }

  private getDeclarationName(declaration: any): string | null {
    if ('getName' in declaration && typeof declaration.getName === 'function') {
      return declaration.getName();
    }
    if (declaration.getKind() === SyntaxKind.Constructor) {
        return "constructor";
    }
    return null;
  }

  private getDeclarationType(declaration: any): Node['type'] {
    const kind = declaration.getKind();
    
    if (kind === SyntaxKind.MethodDeclaration) return 'Method';
    if (kind === SyntaxKind.FunctionDeclaration) return 'Function';
    if (kind === SyntaxKind.ClassDeclaration) return 'Class';
    if (kind === SyntaxKind.InterfaceDeclaration) return 'Interface';
    if (kind === SyntaxKind.Constructor) return 'Method';
    
    return 'Function';
  }

  public exportToJSON(outputPath: string, manifestFiles: ManifestFile[]): void {
    const kgData = this.analyze(manifestFiles);

    // --- CORRECTION CRITIQUE (POC 123.C) ---
    // Les 'flatProps' n'ont plus les champs corrompus du manifest.
    const flatNodes = kgData.nodes.map(node => {
        const flatProps = {
            id: node.id,
            name: node.name,
            filePath: node.filePath, // Déjà relatif et normalisé
            line: node.line,
            type: node.type,
            ...(node.properties || {})
        };
        // 'path' et 'relative_path' n'existent plus ici
        return flatProps;
    });
    // --- FIN CORRECTION ---
    
    const flatEdges = kgData.edges.map(edge => {
         const flatProps = {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            type: edge.type,
            ...(edge.properties || {})
        };
        return flatProps;
    });
    
    const loadReadyKG = {
        nodes: flatNodes,
        edges: flatEdges
    };

    const json = JSON.stringify(loadReadyKG, null, 2);
    fs.writeFileSync(outputPath, json, 'utf-8');
    console.log(`\n💾 Knowledge Graph (prêt pour ETL) exporté vers : ${outputPath}`);
  }
}

// ===== UTILISATION =====

const PROJECT_PATH = process.argv[2];

if (!PROJECT_PATH) {
    console.error("❌ ERREUR : Veuillez fournir le chemin du projet à analyser.");
    console.log("Usage: npx tsx src/graph_builder/ts_kg_analyzer.ts <chemin_projet>");
    process.exit(1);
}

const CWD = process.cwd();
const OUTPUT_JSON = path.join(CWD, 'knowledge-graph.json');
const MANIFEST_JSON = path.join(CWD, 'project_manifest_detailed.json');

console.log('🚀 Analyseur TypeScript vers Knowledge Graph (Piloté par Manifest SOTA)\n');
console.log(`📂 Chemin du projet : ${PROJECT_PATH}`);
console.log(`📖 Lecture du manifest : ${MANIFEST_JSON}`);
console.log('');

try {
  if (!fs.existsSync(MANIFEST_JSON)) {
      console.error(`❌ ERREUR : Manifest introuvable : ${MANIFEST_JSON}`);
      console.log('Veuillez d\'abord exécuter l\'étape 1 (Audit) depuis main.py');
      process.exit(1);
  }
  const manifestContent = fs.readFileSync(MANIFEST_JSON, 'utf-8');
  const manifestFiles: ManifestFile[] = JSON.parse(manifestContent);

  const analyzer = new TypeScriptKGAnalyzer(PROJECT_PATH);
  analyzer.exportToJSON(OUTPUT_JSON, manifestFiles);
  
  console.log('\n✨ Analyse terminée avec succès !');

} catch (error) {
  console.error("\n❌ Erreur lors de l'analyse :", error);
  process.exit(1);
}