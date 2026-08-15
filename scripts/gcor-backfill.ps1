param(
    [string]$BaseUrl = "http://127.0.0.1:5001",
    [string]$Secret = "",
    [int]$Limit = 1000,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
if (-not $Secret) {
    $envFile = Join-Path $PSScriptRoot "..\.env"
    $line = Get-Content $envFile | Where-Object { $_ -match '^STACK_API_SECRET=' } | Select-Object -First 1
    if (-not $line) { $line = Get-Content $envFile | Where-Object { $_ -match '^INGEST_WEBHOOK_SECRET=' } | Select-Object -First 1 }
    $Secret = ($line -split '=', 2)[1]
}
$body = @{ dry_run = -not $Apply; limit = $Limit } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/recovery/backfill" -Headers @{ "X-Gcor-Webhook-Secret" = $Secret } -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 8
