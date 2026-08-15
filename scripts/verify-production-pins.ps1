[CmdletBinding()]
param(
    [string]$ComposeFile = "docker-compose.production.lock.yml"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "Missing $ComposeFile. Copy docker-compose.production.lock.example.yml and replace every sha256 placeholder."
}

$images = @(docker compose -f docker-compose.yml -f $ComposeFile config --images)
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose could not render the production configuration."
}

$invalid = @($images | Where-Object {
    $_ -notmatch '@sha256:[0-9a-f]{64}$' -and $_ -notmatch '^gbuzz-[a-z0-9-]+:[0-9a-f]{7,40}$'
})
if ($invalid.Count -gt 0) {
    throw "Production images must use a sha256 digest or a commit-addressed Gbuzz tag:`n$($invalid -join "`n")"
}

Write-Host "Verified $($images.Count) immutable production image references."
