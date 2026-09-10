[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [ValidateRange(10, 600)][int]$WaitTimeoutSeconds = 90
)

# Target an already-running base-Compose proxy only. Never start dependencies,
# run migrations, remove volumes, or silently downgrade a production overlay.
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot 'docker-compose.yml'
$managedOverlay = [IO.Path]::GetFullPath((Join-Path $repoRoot 'docker-compose.safeguards.override.json'))
# Deployments created before the overlay moved out of backups/ still carry this label.
$legacyOverlay = [IO.Path]::GetFullPath((Join-Path $repoRoot 'backups/gcor-safeguards.override.json'))
$composeArgs = @('compose', '-f', $composeFile)
$mutex = [Threading.Mutex]::new($false, 'GbuzzStackAutoUpdate')
$hasLock = $false
$overlayPath = $null

function Wait-Proxy([string]$Endpoint) {
    $deadline = (Get-Date).AddSeconds($WaitTimeoutSeconds)
    do {
        $proxyId = & docker @composeArgs ps -q gcor-proxy
        if ($LASTEXITCODE -eq 0 -and $proxyId) {
            $probe = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/$Endpoint', timeout=5)"
            & docker exec $proxyId python -c $probe 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "GCOR did not pass $Endpoint within the rollout deadline."
}

function Set-Overlay([string]$Image) {
    @{ services = @{ 'gcor-proxy' = @{ image = $Image; pull_policy = 'never' } } } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $overlayPath -Encoding utf8
}

Push-Location $repoRoot
try {
    $hasLock = $mutex.WaitOne(0)
    if (-not $hasLock) { throw 'Another stack update is already running.' }
    & docker @composeArgs config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed.' }
    $proxyId = & docker @composeArgs ps -q gcor-proxy
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the proxy.' }
    if (-not $proxyId) {
        if ($CheckOnly) { Write-Output 'No running GCOR proxy. No services changed.'; return }
        throw 'No running GCOR proxy to safely replace. Use the documented initial stack setup first.'
    }
    $activeFiles = & docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' $proxyId
    if ($LASTEXITCODE -ne 0 -or $activeFiles -notin @($composeFile, "$composeFile,$managedOverlay", "$composeFile,$legacyOverlay")) {
        throw 'Proxy uses a different Compose configuration. Use its approved deployment workflow to preserve overlays.'
    }
    $oldImage = & docker inspect --format '{{.Image}}' $proxyId
    if ($LASTEXITCODE -ne 0 -or $oldImage -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Unable to identify rollback image.' }
    Wait-Proxy 'health'
    $schemaProbe = @'
import asyncio, asyncpg, main
async def check():
    options = {"dsn": main.POSTGRES_DSN} if main.POSTGRES_DSN else {"host": main.POSTGRES_HOST, "port": main.POSTGRES_PORT, "user": main.POSTGRES_USER, "password": main.POSTGRES_PASSWORD, "database": main.POSTGRES_DB}
    connection = await asyncpg.connect(**options, timeout=5)
    try:
        assert await connection.fetchval("SELECT to_regclass('gcor.governance_outbox')", timeout=5), "Apply governance migration 0007 before rollout"
    finally:
        await connection.close()
asyncio.run(check())
'@
    & docker exec $proxyId python -c $schemaProbe
    if ($LASTEXITCODE -ne 0) { throw 'Governance migration 0007 is required before rollout; running proxy is unchanged.' }
    if ($CheckOnly) { Write-Output 'Proxy is healthy and eligible for guarded rollout. No services changed.'; return }

    $releaseId = [Guid]::NewGuid().ToString('N')
    $candidateImage = "gbuzz-gcor-safeguards:$releaseId"
    $rollbackImage = "gbuzz-gcor-rollback:$releaseId"
    & docker image tag $oldImage $rollbackImage
    if ($LASTEXITCODE -ne 0) { throw 'Unable to retain rollback image.' }
    & docker build --file proxy/Dockerfile --tag $candidateImage .
    if ($LASTEXITCODE -ne 0) { throw 'Build failed; running proxy is unchanged.' }

    New-Item -ItemType Directory -Path (Split-Path -Parent $managedOverlay) -Force | Out-Null
    $overlayPath = $managedOverlay
    Set-Overlay $candidateImage
    $rolloutArgs = $composeArgs + @('-f', $overlayPath)
    try {
        & docker @rolloutArgs up -d --no-deps --no-build --pull never gcor-proxy
        if ($LASTEXITCODE -ne 0) { throw 'Proxy recreation failed.' }
        Wait-Proxy 'health/ready'
        Write-Output "GCOR safeguards deployed and ready. Retained rollback image: $rollbackImage"
    }
    catch {
        $rolloutError = $_.Exception.Message
        Set-Overlay $rollbackImage
        & docker @rolloutArgs up -d --no-deps --no-build --pull never gcor-proxy
        if ($LASTEXITCODE -ne 0) { throw "Rollout failed ($rolloutError); rollback also failed. Retained image: $rollbackImage" }
        Wait-Proxy 'health'
        throw "Rollout failed ($rolloutError). Previous proxy image restored and healthy: $rollbackImage"
    }
}
finally {
    Pop-Location
    if ($hasLock) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
