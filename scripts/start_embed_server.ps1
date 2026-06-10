# Serveur d'embeddings dedie : nomic-embed-code (7B, SOTA code) via llama.cpp Vulkan (GPU AMD).
# A lancer AVANT toute indexation/requete utilisant les embeddings.
# Endpoint expose : http://localhost:8090/v1/embeddings  (OpenAI-compatible)
$ErrorActionPreference = "Stop"

$gguf = "C:\Users\admin\.cache\lm-studio\models\mradermacher\nomic-embed-code-GGUF\nomic-embed-code.Q4_K_S.gguf"
$srv  = Join-Path $PSScriptRoot "..\tools\llamacpp\llama-server.exe"

if (-not (Test-Path $gguf)) { throw "GGUF introuvable : $gguf" }
if (-not (Test-Path $srv))  { throw "llama-server.exe introuvable : $srv" }

Write-Host "Lancement nomic-embed-code (embeddings) sur :8090 ..."
& $srv -m $gguf `
    --embeddings --pooling last `
    -ngl 99 `
    --host 0.0.0.0 --port 8090 `
    -c 4096 -b 4096 -ub 2048
