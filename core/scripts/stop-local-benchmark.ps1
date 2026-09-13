$ErrorActionPreference = "Stop"
$repository = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$assetDirectory = Join-Path $repository ".local-models"
$pidFile = Join-Path $assetDirectory "llama-server.pid"
if (!(Test-Path -LiteralPath $pidFile)) { Write-Output "No recorded benchmark process."; return }
$benchmarkPid = [int](Get-Content -LiteralPath $pidFile -Raw)
$benchmarkProcess = Get-Process -Id $benchmarkPid -ErrorAction SilentlyContinue
if (!$benchmarkProcess) { Write-Output "Benchmark process already stopped."; return }
$expectedServer = Join-Path $assetDirectory "llama-b10936-vulkan\llama-server.exe"
if ($benchmarkProcess.Path -ne $expectedServer) {
    throw "Recorded PID belongs to a different executable; no process was stopped."
}
Stop-Process -Id $benchmarkPid
Write-Output "Stopped local benchmark process $benchmarkPid. Model files are retained."
