param(
    [string]$BaseUrl = "http://127.0.0.1:5001",
    [string]$Secret = "",
    [int]$Limit = 100
)

$ErrorActionPreference = "Stop"
if (-not $Secret) {
    $line = Get-Content (Join-Path $PSScriptRoot "..\.env") | Where-Object { $_ -match '^STACK_API_SECRET=' } | Select-Object -First 1
    if (-not $line) {
        $line = Get-Content (Join-Path $PSScriptRoot "..\.env") | Where-Object { $_ -match '^INGEST_WEBHOOK_SECRET=' } | Select-Object -First 1
    }
    $Secret = ($line -split '=', 2)[1]
}

$headers = @{ "X-Gcor-Webhook-Secret" = $Secret }
$records = Invoke-RestMethod -Uri "$BaseUrl/api/recovery/records?limit=$Limit&verify_objects=true" -Headers $headers
$summary = [ordered]@{
    checked_at = [DateTimeOffset]::UtcNow.ToString("o")
    record_count = @($records).Count
    indexed_count = @($records | Where-Object { $_.status -eq 'indexed' }).Count
    recoverable_count = @($records | Where-Object { $_.recoverable -eq $true }).Count
    buckets = @($records.bucket | Sort-Object -Unique)
    records = $records
}
$summary | ConvertTo-Json -Depth 8
