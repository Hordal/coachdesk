param([switch]$Rebuild)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExe = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if ($Rebuild) { python setup_local.py --rebuild } else { python setup_local.py }
if ($LASTEXITCODE -ne 0) { throw 'Local setup failed.' }
& $pythonExe setup_analysis.py --if-needed
if ($LASTEXITCODE -ne 0) { throw 'Analysis setup failed.' }
& $pythonExe run_local.py
