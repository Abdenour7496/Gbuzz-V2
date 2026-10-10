[CmdletBinding()]
param(
    [string]$Image = 'ghcr.io/block/buzz:sha-7ebf1be',
    [int]$WaitTimeoutSeconds = 600,
    [switch]$SkipBackup
)
# Upgrades the Buzz relay to the build matching Buzz Desktop v0.5.26.
# Order: encrypted backup -> pull -> pin digest -> rebuild workspace images
# (pinned knowledge-trust images excluded) -> recreate containers -> verify.
$ErrorActionPreference = 'Continue'  # native stderr must not abort; failures are checked via exit codes
$repoRoot = Split-Path -Parent $PSScriptRoot
$diag = Join-Path $repoRoot '_diag'
New-Item -ItemType Directory -Force $diag | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $diag "upgrade-relay-$stamp.log"
$statusFile = Join-Path $diag 'upgrade-relay.status'
function Set-Status([string]$s) { Set-Content -LiteralPath $statusFile -Value "$((Get-Date).ToString('o')) $s" -Encoding utf8 }
function Step([string]$m) { Write-Host "`n=== $m ==="; Set-Status "RUNNING $m" }
function Invoke-Native([scriptblock]$c, [string]$fail) { & $c; if ($LASTEXITCODE -ne 0) { throw "$fail (exit $LASTEXITCODE)" } }

Start-Transcript -LiteralPath $log | Out-Null
Set-Status 'STARTED'
Push-Location $repoRoot
try {
    Import-Module (Join-Path $PSScriptRoot 'ComposePlan.psm1') -Force -ErrorAction Stop
    $plan = Get-GbuzzComposePlan $repoRoot (Join-Path $repoRoot 'config/windows-startup.compose-files.json')
    $ca = @('compose') + $plan.Arguments

    Step 'Docker engine'
    Invoke-Native { docker info --format '{{.ServerVersion}}' } 'Docker Engine is unavailable'

    Step 'Current relay'
    docker compose @($plan.Arguments) ps relay --format '{{.Image}} {{.Status}}'

    if (-not $SkipBackup) {
        Step 'Encrypted, verified stack backup (scripts/backup-stack.ps1)'
        $backup = & (Join-Path $PSScriptRoot 'backup-stack.ps1') -RepoRoot $repoRoot
        Write-Host "Backup: $backup"
        # Extra plain relay-database dump kept beside the repo for a fast rollback.
        $cfg = docker @ca config --format json | ConvertFrom-Json
        $pgUser = $cfg.services.postgres.environment.POSTGRES_USER; $pgDb = $cfg.services.postgres.environment.POSTGRES_DB
        $dumpDir = Join-Path $repoRoot 'backups\pre-buzz-0.5.26'; New-Item -ItemType Directory -Force $dumpDir | Out-Null
        Invoke-Native { docker @ca exec -T postgres pg_dump -U $pgUser -d $pgDb -Fc -f /tmp/pre-0526.dump } 'pg_dump failed'
        Invoke-Native { docker @ca cp postgres:/tmp/pre-0526.dump (Join-Path $dumpDir "postgres-$stamp.dump") } 'dump copy failed'
        docker @ca exec -T postgres rm -f /tmp/pre-0526.dump | Out-Null
        $prev = docker @ca images relay --format '{{.Repository}}:{{.Tag}} {{.ID}}'
        "previous relay image: $prev" | Set-Content (Join-Path $dumpDir "rollback-$stamp.txt")
        Write-Host "Relay DB dump: $dumpDir\postgres-$stamp.dump"
    }

    Step "Pull $Image"
    Invoke-Native { docker pull $Image } 'Image pull failed'
    $digestRef = (docker image inspect $Image --format '{{range .RepoDigests}}{{println .}}{{end}}' | Where-Object { $_ -like 'ghcr.io/block/buzz@sha256:*' } | Select-Object -First 1).Trim()
    if ($digestRef -notmatch '^ghcr\.io/block/buzz@sha256:[0-9a-f]{64}$') { throw "Could not resolve digest for $Image" }
    $src = docker image inspect $Image --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
    Write-Host "Digest: $digestRef  (revision label: $src)"

    Step 'Pin digest in .env and docker-compose.production.lock.yml'
    foreach ($f in @('.env', 'docker-compose.production.lock.yml')) {
        $p = Join-Path $repoRoot $f
        $raw = [IO.File]::ReadAllText($p)
        $new = if ($f -eq '.env') { [regex]::Replace($raw, '(?m)^BUZZ_IMAGE=[^\r\n]*', "BUZZ_IMAGE=$digestRef") }
               else { [regex]::Replace($raw, 'ghcr\.io/block/buzz@sha256:[0-9a-f]{64}', $digestRef) }
        if ($new -ne $raw) { [IO.File]::WriteAllText($p, $new, [Text.UTF8Encoding]::new($false)); Write-Host "updated $f" } else { Write-Host "no change in $f" }
    }

    Step 'Resolve compose plan'
    $cfg = docker @ca config --format json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'compose config failed' }
    Assert-GbuzzRequiredServices $plan $cfg
    $buildable = @($cfg.services.PSObject.Properties | Where-Object {
        $_.Value.PSObject.Properties.Name -contains 'build' -and $_.Value.pull_policy -ne 'never' } | ForEach-Object Name)
    $pinned = @($cfg.services.PSObject.Properties | Where-Object { $_.Value.pull_policy -eq 'never' } | ForEach-Object { "$($_.Name)=$($_.Value.image)" })
    Write-Host "Rebuilding: $($buildable -join ', ')"
    Write-Host "Kept as pinned release images (not rebuilt): $($pinned -join ', ')"

    Step 'Rebuild workspace images'
    if ($buildable.Count) { Invoke-Native { docker @ca build @buildable } 'Image build failed' }

    Step 'Recreate containers and wait for health'
    Invoke-Native { docker @ca up -d --no-build --force-recreate --remove-orphans --wait --wait-timeout $WaitTimeoutSeconds } "Stack did not become healthy within $WaitTimeoutSeconds s"

    Step 'Verify'
    docker @ca ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
    $relayImg = docker @ca ps relay --format '{{.Image}}'
    Write-Host "relay image: $relayImg"
    $pgUser = $cfg.services.postgres.environment.POSTGRES_USER; $pgDb = $cfg.services.postgres.environment.POSTGRES_DB
    docker @ca exec -T postgres psql -U $pgUser -d $pgDb -At -c "SELECT 'latest relay migration: ' || max(version) || ' (' || count(*) || ' applied)' FROM _sqlx_migrations WHERE success" 2>&1
    Write-Host '--- relay log (tail) ---'
    docker @ca logs relay --tail 60 2>&1
    $containers = docker @ca ps --format json | ForEach-Object { $_ | ConvertFrom-Json } | ForEach-Object {
        [pscustomobject]@{ Service = $_.Service; Name = $_.Name; Health = $_.Health; ConfigFiles = @() } }
    Assert-GbuzzRuntimePlan $plan $containers
    Write-Host 'All required services running and not unhealthy.'
    Set-Status "SUCCESS relay=$digestRef"
}
catch {
    Write-Host "FAILED: $($_.Exception.Message)"
    Set-Status "FAILED $($_.Exception.Message)"
}
finally {
    Pop-Location
    Stop-Transcript | Out-Null
}
