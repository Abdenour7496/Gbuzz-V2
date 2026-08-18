param(
    [ValidateRange(0, 86400)][int]$WaitSeconds = 0,
    [ValidateRange(1, 300)][int]$PollSeconds = 5,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $repoRoot
try {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($WaitSeconds)
    do {
        $output = & docker compose exec -T graphiti-projector python control.py gate
        $exitCode = $LASTEXITCODE
        if ($exitCode -eq 0 -or [DateTimeOffset]::UtcNow -ge $deadline) { break }
        Start-Sleep -Seconds $PollSeconds
    } while ($true)
    if ($Json) { $output } else {
        $state = $output | ConvertFrom-Json
        Write-Output "Graphiti projection release gate: pending=$($state.after.pending), retryable=$($state.after.retryable), in_flight=$($state.after.in_flight), dead_letter=$($state.after.dead_letter)"
    }
    exit $exitCode
}
finally { Pop-Location }
