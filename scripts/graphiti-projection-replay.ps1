param(
    [ValidateRange(1, 100)][int]$Limit = 10,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $repoRoot
try {
    $output = & docker compose exec -T graphiti-projector python control.py replay --limit $Limit
    if ($LASTEXITCODE -ne 0) { throw "Graphiti bounded replay failed" }
    if ($Json) { $output } else {
        $state = $output | ConvertFrom-Json
        Write-Output "Reconciled $($state.reconciled); requeued $(@($state.requeued_entry_ids).Count) of at most $Limit; authoritative writes=$($state.authoritative_writes)"
        Write-Output "Remaining: pending=$($state.after.pending), retryable=$($state.after.retryable), in_flight=$($state.after.in_flight), dead_letter=$($state.after.dead_letter)"
    }
}
finally { Pop-Location }
