# Reset local Navigator pipeline artifacts (output/ + sqlite cache).
# Usage:
#   .\scripts\reset_local_navigator.ps1              # Superstore only
#   .\scripts\reset_local_navigator.ps1 -All         # all pipeline JSON + sqlite

param(
    [switch]$All
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Output = Join-Path $Root "output"
$Db = Join-Path $Root "navigator.db"

function Remove-Matching {
    param([string]$Pattern)
    if (-not (Test-Path $Output)) { return 0 }
    $files = Get-ChildItem $Output -File -Filter $Pattern -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        Remove-Item $f.FullName -Force
    }
    return $files.Count
}

if ($All) {
    $n1 = Remove-Matching "intelligence_config_*.json"
    $n2 = Remove-Matching "inventory_*.json"
    Remove-Item (Join-Path $Output "run_metrics_latest.json") -Force -ErrorAction SilentlyContinue
    Write-Host "Removed $n1 intelligence configs, $n2 inventory files"
} else {
    $n1 = Remove-Matching "intelligence_config_Superstore_*.json"
    $n2 = Remove-Matching "inventory_Superstore_*.json"
    Write-Host "Removed $n1 Superstore configs, $n2 Superstore inventory files"
}

if (Test-Path $Db) {
    python -c @"
import sqlite3, os
db = r'$Db'
if os.path.exists(db):
    con = sqlite3.connect(db)
    for t in ('intelligence_configs', 'pipeline_runs', 'companies'):
        try:
            con.execute(f'delete from {t}')
        except Exception:
            pass
    con.commit()
    con.close()
    print('Cleared navigator.db tables: companies, pipeline_runs, intelligence_configs')
"@
} else {
    Write-Host "No navigator.db found (ok)"
}

Write-Host "Done. Restart Navigator API if it is running."
