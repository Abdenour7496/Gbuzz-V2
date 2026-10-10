<#
  Gbuzz ingestion diagnosis + end-to-end smoke test.
    powershell -ExecutionPolicy Bypass -File C:\Gbuzz\scripts\diagnose-ingestion.ps1            # diagnose + smoke test
    powershell -ExecutionPolicy Bypass -File C:\Gbuzz\scripts\diagnose-ingestion.ps1 -ApplyFix  # rebuild projector with the retry fix first
  Output is also written to C:\Gbuzz\_diag\ingestion-report.txt
#>
[CmdletBinding()]
param([string]$RepoRoot='C:\Gbuzz',[string]$ConfigPath='C:\Gbuzz\config\windows-startup.compose-files.json',[switch]$ApplyFix)
$ErrorActionPreference='Continue'
Import-Module (Join-Path $RepoRoot 'scripts\ComposePlan.psm1') -Force
$plan=Get-GbuzzComposePlan $RepoRoot $ConfigPath
$report=Join-Path $RepoRoot '_diag\ingestion-report.txt'
New-Item -ItemType Directory -Force (Split-Path $report) | Out-Null
Start-Transcript -Path $report -Force | Out-Null
Push-Location $RepoRoot
try {
  if ($ApplyFix) {
    Write-Host '== Rebuilding gcor-event-projector (retry backoff fix) =='
    & docker compose @($plan.Arguments) up -d --build gcor-event-projector
    Start-Sleep 15
  }
  Write-Host '== Containers =='
  & docker compose @($plan.Arguments) ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
  Write-Host '== Projector logs (last 40) =='
  & docker compose @($plan.Arguments) logs --tail 40 gcor-event-projector
  Write-Host '== Proxy errors (last 200 lines, filtered) =='
  & docker compose @($plan.Arguments) logs --tail 200 gcor-proxy 2>&1 | Select-String -Pattern 'error|exception|traceback|\s(4|5)\d\d\s' | Select-Object -Last 25
  Write-Host '== Ingest rate (should fall to ~0 when idle after the fix) =='
  $a=(Invoke-WebRequest http://127.0.0.1:5001/metrics -UseBasicParsing).Content -split "`n" | Select-String '^gcor_ingest_total '
  Start-Sleep 30
  $b=(Invoke-WebRequest http://127.0.0.1:5001/metrics -UseBasicParsing).Content -split "`n" | Select-String '^gcor_ingest_total '
  $delta=[double]($b.ToString().Split(' ')[1])-[double]($a.ToString().Split(' ')[1]); Write-Host ("ingests in 30s: {0}" -f $delta)
  Write-Host '== Failed projections + end-to-end smoke ingest =='
  $cid=(& docker compose @($plan.Arguments) ps -q gcor-event-projector)
  & docker cp (Join-Path $RepoRoot '_diag\smoke_ingest.py') "${cid}:/tmp/smoke_ingest.py"
  & docker compose @($plan.Arguments) exec -T -w /app gcor-event-projector sh -c 'PYTHONPATH=/app:. python /tmp/smoke_ingest.py'
} finally { Pop-Location; Stop-Transcript | Out-Null; Write-Host "Report: $report" }
