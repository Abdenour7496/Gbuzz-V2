[CmdletBinding()]
param(
    [string]$Tag = 'desktop-v0.5.26',
    [string]$Commit = '2b4b138dc5cf2d9cc1a0ceb21d9063ff56fe8bf4',
    [string]$Version = '0.5.26'
)
# Builds the Buzz Desktop knowledge-card package on the official 0.5.26 sources,
# bundling the official 0.5.26 sidecars. Does NOT install anything.
$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent $PSScriptRoot
$diag = Join-Path $repoRoot '_diag'; New-Item -ItemType Directory -Force $diag | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $diag "upgrade-desktop-$stamp.log"
$statusFile = Join-Path $diag 'upgrade-desktop.status'
function Set-Status([string]$s) { Set-Content -LiteralPath $statusFile -Value "$((Get-Date).ToString('o')) $s" -Encoding utf8 }
function Step([string]$m) { Write-Host "`n=== $m ==="; Set-Status "RUNNING $m" }
function Invoke-Native([scriptblock]$c, [string]$fail) { & $c; if ($LASTEXITCODE -ne 0) { throw "$fail (exit $LASTEXITCODE)" } }

Start-Transcript -LiteralPath $log | Out-Null
Set-Status 'STARTED'
try {
    $sourceRepo = Join-Path $repoRoot 'backups\buzz-source'
    $worktree = Join-Path $repoRoot 'backups\buzz-desktop-source'
    $sidecarSrc = Join-Path $repoRoot 'backups\buzz-0.5.26-official-sidecars'
    $binDir = Join-Path $worktree 'desktop\src-tauri\binaries'
    $saveDir = Join-Path $repoRoot "backups\buzz-desktop-before-$Version"
    New-Item -ItemType Directory -Force $saveDir | Out-Null

    Step 'Save current worktree state'
    $before = 'b9392d9d78744df365f9276e1ffe8c1baa5ea903'  # 0.5.23 card build; refined below on first run
    if (Test-Path (Join-Path $saveDir 'binaries')) { Write-Host "Pre-upgrade state already saved in $saveDir; keeping it." } else {
    $before = (git -C $worktree rev-parse HEAD).Trim()
    Write-Host "worktree HEAD: $before"
    git -C $worktree diff | Set-Content -LiteralPath (Join-Path $saveDir 'worktree.diff') -Encoding utf8
    git -C $worktree status --porcelain | Set-Content -LiteralPath (Join-Path $saveDir 'worktree.status') -Encoding utf8
    New-Item -ItemType Directory -Force (Join-Path $saveDir 'binaries') | Out-Null
    Copy-Item (Join-Path $binDir '*.exe') (Join-Path $saveDir 'binaries') -Force
    }

    Step "Fetch $Tag"
    Invoke-Native { git -C $sourceRepo fetch --depth 1 origin tag $Tag } 'git fetch failed'
    $resolved = (git -C $sourceRepo rev-parse "$Tag^{commit}").Trim()
    if ($resolved -ne $Commit) { throw "Tag $Tag resolves to $resolved, expected $Commit" }

    Step "Check out $Tag in the isolated worktree"
    Invoke-Native { git -C $worktree checkout -f --detach $Commit } 'checkout failed'
    $pkg = Get-Content -Raw (Join-Path $worktree 'desktop\package.json') | ConvertFrom-Json
    if ($pkg.version -ne $Version) { throw "desktop/package.json is $($pkg.version), expected $Version" }

    Step 'Install official 0.5.26 sidecars'
    $sums = @{}; Get-Content (Join-Path $sidecarSrc 'SHA256SUMS') | ForEach-Object { $h, $n = $_ -split '\s+', 2; $sums[$n.Trim()] = $h.ToUpper() }
    $sidecars = @()
    foreach ($n in 'buzz-acp', 'buzz-agent', 'buzz-dev-mcp', 'buzz', 'git-credential-nostr') {
        $src = Join-Path $sidecarSrc "$n.exe"
        $h = (Get-FileHash -Algorithm SHA256 $src).Hash
        if ($h -ne $sums["$n.exe"]) { throw "Sidecar hash mismatch: $n" }
        Copy-Item $src (Join-Path $binDir "$n-x86_64-pc-windows-msvc.exe") -Force
        $sidecars += [ordered]@{ name = $n; sha256 = $h }
        Write-Host "$n $h"
    }

    Step 'Apply knowledge cards and build frontend'
    $envLocal = Join-Path $worktree 'desktop\.env.local'
    $pk = ((Get-Content $envLocal | Where-Object { $_ -match '^VITE_BUZZ_KNOWLEDGE_PUBKEY=' }) -split '=', 2)[1].Trim()
    if ($pk -notmatch '^[0-9a-f]{64}$') { throw 'VITE_BUZZ_KNOWLEDGE_PUBKEY missing from desktop/.env.local' }
    & (Join-Path $PSScriptRoot 'prepare-buzz-desktop-cards.ps1') -SourceRoot $worktree -KnowledgePubkey $pk -Build
    if (-not $?) { throw 'prepare-buzz-desktop-cards.ps1 failed' }

    Step 'Native Windows build (Rust + NSIS) - this takes a while'
    $env:VITE_BUZZ_KNOWLEDGE_PUBKEY = $pk
    # Run in a child process so cargo/tauri output (stderr included) is captured to a file.
    $nativeLog = Join-Path $diag "native-build-$stamp.txt"
    Write-Host "Native build output: $nativeLog"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build-buzz-desktop-windows.ps1') -SourceRoot $worktree -ToolsRoot (Join-Path $repoRoot 'backups\build-tools') 2>&1 |
        ForEach-Object { "$_" } | Tee-Object -FilePath $nativeLog
    if ($LASTEXITCODE -ne 0) { throw "Native build failed (exit $LASTEXITCODE); see $nativeLog" }
    $built = Get-ChildItem (Join-Path $worktree 'desktop\src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis') -Filter "*$Version*setup*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $built) { throw 'No 0.5.26 NSIS installer produced' }

    Step 'Record release'
    $dest = Join-Path $repoRoot "backups\releases\Buzz-$Version-knowledge-cards-setup.exe"
    Copy-Item $built.FullName $dest -Force
    $exe = Join-Path $worktree 'desktop\src-tauri\target\x86_64-pc-windows-msvc\release\buzz-desktop.exe'
    $manifest = [ordered]@{
        desktop_version = $Version; source_tag = $Tag; source_commit = $Commit; previous_source_commit = $before
        target = 'x86_64-pc-windows-msvc'; sidecars = $sidecars
        sidecar_origin = "official Buzz_$($Version)_x64-setup_alpha-unsigned.exe"
        installer = [ordered]@{ path = $dest; bytes = (Get-Item $dest).Length; sha256 = (Get-FileHash -Algorithm SHA256 $dest).Hash }
        native_executable_sha256 = if (Test-Path $exe) { (Get-FileHash -Algorithm SHA256 $exe).Hash } else { $null }
        built_at = (Get-Date).ToUniversalTime().ToString('o'); installed = $false
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $repoRoot "backups\buzz-cards-build-manifest-$Version.json") -Encoding utf8
    Write-Host "Installer: $dest"
    Set-Status "SUCCESS $dest"
}
catch {
    Write-Host "FAILED: $($_.Exception.Message)"
    Set-Status "FAILED $($_.Exception.Message)"
}
finally { Stop-Transcript | Out-Null }
