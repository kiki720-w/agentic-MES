param(
    [ValidateRange(1024, 65535)][int]$Port = 11434,
    [string]$Device = "Vulkan0",
    [ValidateSet("8B", "4B")][string]$ModelSize = "8B"
)

$ErrorActionPreference = "Stop"
$repository = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$assetDirectory = Join-Path $repository ".local-models"
$server = Join-Path $assetDirectory "llama-b10936-vulkan\llama-server.exe"
$modelFile = if ($ModelSize -eq "8B") {
    Join-Path $assetDirectory "Qwen3-8B-Q4_K_M.gguf"
} else {
    Join-Path $assetDirectory "Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
}
$modelAlias = if ($ModelSize -eq "8B") { "qwen3-8b-q4_k_m" } else { "qwen3-4b-instruct-2507-q4_k_m" }
if (!(Test-Path -LiteralPath $server) -or !(Test-Path -LiteralPath $modelFile)) {
    throw "Run core/scripts/setup-local-benchmark.py first to download and verify the pinned assets."
}
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use; no existing process was stopped."
}
$arguments = @(
    "--model", ('"{0}"' -f $modelFile), "--host", "127.0.0.1", "--port", $Port,
    "--alias", $modelAlias, "--ctx-size", "8192",
    "--parallel", "1", "--n-gpu-layers", "99", "--device", $Device,
    "--reasoning", "off",
    "--offline", "--no-webui", "--log-disable"
)
$localProcess = Start-Process -FilePath $server -ArgumentList $arguments -WindowStyle Hidden -PassThru
Set-Content -LiteralPath (Join-Path $assetDirectory "llama-server.pid") -Value $localProcess.Id
Write-Output "Local benchmark PID: $($localProcess.Id)"
Write-Output "Endpoint: http://127.0.0.1:$Port/v1"
Write-Output "Model: $modelAlias"
