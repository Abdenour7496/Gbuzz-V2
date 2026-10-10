<#
.SYNOPSIS
  Release "knowledge loop" v1: agents read the GCOR brain with their own Buzz
  identity, propose what they learn, and humans approve in Buzz.

.DESCRIPTION
  1. Preflight: Docker, disk, running compose file set (drift vs startup plan).
  2. Verified encrypted backup (scripts/backup-stack.ps1) unless -SkipBackup.
  3. Build a new trust image and run the security gate tests inside it.
  4. Point GCOR_TRUST_IMAGE at it and recreate only gcor-proxy, gcor-enterprise
     and buzz-knowledge. Automatic rollback if they do not become healthy.
  5. Enroll the target channel(s) for !knowledge commands and report which
     agents in them have an active human sponsor.
  6. Install the agent protocol (skill, AGENTS.md section, CLAUDE.md pointer)
     into the Buzz nest and register the MCP server for Claude Code.
  7. Verify the signed agent path end to end with a throwaway identity.

  Rollback: .\scripts\release-knowledge-loop.ps1 -Rollback
#>
[CmdletBinding()]
param(
    [string[]]$ChannelName = @('Kowledge Brain'),
    [string]$Tag = 'gbuzz-knowledge-durable:knowledge-loop-20261010',
    [switch]$SkipBackup,
    [switch]$SkipAgentInstall,
    [switch]$NoMcpRegistration,
    [switch]$Rollback,
    [int]$WaitTimeoutSeconds = 300,
    [int]$MinFreeGB = 8
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot '.env'
$services = @('gcor-proxy', 'gcor-enterprise', 'buzz-knowledge')
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$evidenceRoot = Join-Path $repoRoot 'backups'
$nest = Join-Path $env:USERPROFILE '.buzz'
$agentCli = Join-Path $repoRoot 'agent-tools\gcor-agent\dist\gcor-agent.mjs'

function Step([string]$Text) { Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Ok([string]$Text) { Write-Host "    OK  $Text" -ForegroundColor Green }
function Warn([string]$Text) { Write-Host "    !!  $Text" -ForegroundColor Yellow }

# Windows PowerShell 5.1 turns native stderr into terminating errors under
# 'Stop'. Run native tools with 'Continue' and judge them by exit code.
function Invoke-Native([string]$File, [string[]]$Arguments, [switch]$Capture, [switch]$AllowFailure) {
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        if ($Capture) { $out = & $File @Arguments 2>$null } else { & $File @Arguments 2>&1 | ForEach-Object { $line = "$_"; if ($line -and $line -ne 'System.Management.Automation.RemoteException') { Write-Host "      $line" } } }
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
    if ($code -ne 0 -and -not $AllowFailure) { throw "$File $($Arguments[0]) $($Arguments[1]) failed (exit $code)" }
    if ($Capture) { return $out }
}

function Get-DotEnvValue([string]$Name) {
    $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -Last 1
    if ($null -eq $line) { return $null }
    return ($line -split '=', 2)[1].Trim()
}

function Set-DotEnvValue([string]$Name, [string]$Value) {
    $lines = [Collections.Generic.List[string]](Get-Content -LiteralPath $envFile)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$([regex]::Escape($Name))=") { $lines[$i] = "$Name=$Value"; $found = $true }
    }
    if (-not $found) { $lines.Add("$Name=$Value") }
    $tmp = "$envFile.tmp-$stamp"
    [IO.File]::WriteAllLines($tmp, $lines, (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $envFile -Force
}

# The running stack is authoritative: the startup plan has drifted before
# (docs: buzz-0.5.26 upgrade). Use the files the containers were created from.
function Get-RunningComposeArgs {
    # Read the raw JSON: Windows PowerShell 5.1 mangles embedded double quotes in
    # native arguments, so a --format template with quoted label keys breaks.
    $inspect = (Invoke-Native docker @('inspect', 'gbuzz-gcor-proxy-1') -Capture) -join "`n" | ConvertFrom-Json
    $label = @($inspect)[0].Config.Labels.'com.docker.compose.project.config_files'
    $files = @(("$label".Trim()) -split ',' | Where-Object { $_ })
    if (-not $files.Count) { throw 'Cannot read the compose file set from gbuzz-gcor-proxy-1. Is the stack running?' }
    if (-not ($files | Where-Object { $_ -like '*knowledge-trust*' })) { throw 'Running stack is not on the knowledge-trust overlay; refusing to release into an unknown baseline.' }
    $composeArgs = @('compose', '--project-name', 'gbuzz')
    foreach ($f in $files) { $composeArgs += @('-f', $f) }
    $plan = Get-Content (Join-Path $repoRoot 'config\windows-startup.compose-files.json') -Raw | ConvertFrom-Json
    foreach ($p in @($plan.profiles)) { $composeArgs += @('--profile', $p) }
    $planned = @($plan.files | ForEach-Object { [IO.Path]::GetFullPath((Join-Path $repoRoot $_)) })
    $running = @($files | ForEach-Object { [IO.Path]::GetFullPath($_) })
    $extra = @($running | Where-Object { $planned -notcontains $_ })
    $missing = @($planned | Where-Object { $running -notcontains $_ })
    return [pscustomobject]@{ Args = $composeArgs; Files = $files; Extra = $extra; Missing = $missing }
}

function Wait-Healthy([object]$Compose) {
    Invoke-Native docker ($Compose.Args + @('up', '-d', '--no-deps', '--wait', '--wait-timeout', "$WaitTimeoutSeconds") + $services)
}

function Test-ClockSkew([int]$LimitSeconds = 20) {
    # Agent proofs are valid for 60 s and are checked inside Docker's VM, whose
    # clock can drift from Windows after sleep/hibernate.
    $vm = Invoke-Native docker @('exec', 'gbuzz-gcor-enterprise-1', 'python', '-c', 'import time;print(int(time.time()))') -Capture -AllowFailure | Select-Object -First 1
    if ("$vm" -notmatch '^\d+$') { Warn 'Could not read the container clock; skipping skew check.'; return }
    $skew = [int64]$vm - [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ([math]::Abs($skew) -gt $LimitSeconds) {
        throw "Docker VM clock differs from Windows by $skew s. Signed agent requests would be rejected. Fix: 'wsl --shutdown' then restart Docker Desktop (or: docker run --rm --privileged alpine hwclock -s), then re-run."
    }
    Ok "Clock skew Windows vs Docker VM: $skew s"
}

function Invoke-Psql([string]$Sql) {
    $user = Get-DotEnvValue 'POSTGRES_USER'; $db = Get-DotEnvValue 'POSTGRES_DB'
    return Invoke-Native docker @('exec', '-i', 'gbuzz-postgres-1', 'psql', '-U', $user, '-d', $db, '-v', 'ON_ERROR_STOP=1', '-qtAF', '|', '-c', $Sql) -Capture
}

function Quote-Sql([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }

Push-Location $repoRoot
try {
    if ($Rollback) {
        Step 'Rolling back to the previous trust image'
        $record = Get-ChildItem $evidenceRoot -Directory -Filter 'knowledge-loop-*' | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'rollback.json' } | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $record) { throw 'No knowledge-loop rollback record found.' }
        $previous = (Get-Content $record -Raw | ConvertFrom-Json).previous_trust_image
        Set-DotEnvValue 'GCOR_TRUST_IMAGE' $previous
        Wait-Healthy (Get-RunningComposeArgs)
        Ok "Restored GCOR_TRUST_IMAGE=$previous (record: $record). Agent protocol files were left in place; they are harmless without the release."
        return
    }

    Step 'Preflight'
    Invoke-Native docker @('info', '--format', '{{.ServerVersion}}') -Capture | Out-Null; Ok 'Docker Engine reachable'
    $freeGB = [math]::Round((Get-PSDrive -Name C).Free / 1GB, 1)
    if ($freeGB -lt $MinFreeGB) { throw "Only $freeGB GB free on C:. Free at least $MinFreeGB GB before building (old images: docker image prune)." }
    Ok "$freeGB GB free on C:"
    $compose = Get-RunningComposeArgs
    Ok "Running compose set: $($compose.Files.Count) files"
    if ($compose.Extra.Count -or $compose.Missing.Count) {
        Warn 'Startup plan (config\windows-startup.compose-files.json) differs from the running stack:'
        $compose.Extra | ForEach-Object { Warn "  running but not in plan: $_" }
        $compose.Missing | ForEach-Object { Warn "  in plan but not running: $_" }
        Warn 'A reboot would start a different stack. Reconcile the plan after this release.'
    }
    Invoke-Native docker ($compose.Args + @('config', '--quiet')); Ok 'Compose configuration valid'
    Test-ClockSkew
    $previousImage = Get-DotEnvValue 'GCOR_TRUST_IMAGE'
    if (-not $previousImage) { throw 'GCOR_TRUST_IMAGE is not set in .env' }
    if ($previousImage -eq $Tag) { Warn "GCOR_TRUST_IMAGE already equals $Tag; rebuilding in place." }
    $evidence = Join-Path $evidenceRoot "knowledge-loop-$stamp"
    New-Item -ItemType Directory -Path $evidence -Force | Out-Null
    $before = @{}
    foreach ($s in $services) { $before[$s] = (Invoke-Native docker @('inspect', '--format', '{{.Image}}', "gbuzz-$s-1") -Capture -AllowFailure | Select-Object -First 1) }
    [ordered]@{ created_at = $stamp; previous_trust_image = $previousImage; new_trust_image = $Tag; container_images = $before; compose_files = $compose.Files } |
        ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $evidence 'rollback.json') -Encoding utf8
    Ok "Rollback record: $evidence\rollback.json"

    if (-not $SkipBackup) {
        Step 'Verified encrypted backup'
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'backup-stack.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Backup failed; nothing was changed.' }
        Ok 'Backup completed'
    } else { Warn 'Backup skipped by request' }

    Step "Build $Tag"
    Invoke-Native docker @('build', '-f', 'proxy/Dockerfile', '-t', $Tag, '.')
    Ok 'Image built'

    Step 'Security gate inside the new image'
    $gateEnv = @('-e', 'POSTGRES_USER=gate', '-e', 'POSTGRES_PASSWORD=gate', '-e', 'POSTGRES_DB=gate', '-e', 'MINIO_ENDPOINT=minio:9000',
        '-e', 'MINIO_ROOT_USER=gate', '-e', 'MINIO_ROOT_PASSWORD=gate', '-e', 'GCOR_PUBLIC_ORIGIN=http://127.0.0.1:5011', '-e', 'PYTHONPATH=/app')
    $gate = @('run', '--rm', '--network', 'none') + $gateEnv + @($Tag, 'python', '-m', 'unittest',
        '-k', 'agent_principal', '-k', 'caller_kind', '-k', 'cannot_review', '-k', 'human_owner', '-k', 'scope_cannot',
        '-k', 'single_use', '-k', 'rechecks_membership', '-k', 'fails_closed', '-k', 'requires_sponsor', '-k', 'signature_and_channel',
        'tests.test_enterprise_access', 'tests.test_buzz_chat')
    Invoke-Native docker $gate
    Ok 'Agent isolation, sponsorship, review authority and replay tests passed in the release image'

    Step 'Deploy (gcor-proxy, gcor-enterprise, buzz-knowledge only)'
    Set-DotEnvValue 'GCOR_TRUST_IMAGE' $Tag
    try {
        Wait-Healthy $compose
        Ok 'Services healthy on the new image'
    } catch {
        Warn "Deployment unhealthy: $($_.Exception.Message). Rolling back to $previousImage"
        Set-DotEnvValue 'GCOR_TRUST_IMAGE' $previousImage
        Wait-Healthy $compose
        throw 'Release rolled back; the previous image is running again.'
    }

    Step 'Enroll channels for chat-native knowledge'
    $botPub = (Invoke-Native docker ($compose.Args + @('exec', '-T', 'buzz-knowledge', 'python', '-c',
        "import os;from coincurve import PrivateKey;print(PrivateKey(bytes.fromhex(os.environ['BUZZ_KNOWLEDGE_PRIVATE_KEY'])).public_key_xonly.format().hex())")) -Capture |
        Where-Object { $_ -match '^[0-9a-f]{64}$' } | Select-Object -First 1)
    if (-not $botPub) { throw 'Could not derive the Knowledge agent public key.' }
    foreach ($name in $ChannelName) {
        $rows = @(Invoke-Psql "SELECT id FROM public.channels WHERE name=$(Quote-Sql $name) AND deleted_at IS NULL AND archived_at IS NULL")
        $rows = @($rows | Where-Object { $_ -match '^[0-9a-f-]{36}$' })
        if ($rows.Count -ne 1) { Warn "Channel '$name': expected exactly one active channel, found $($rows.Count). Skipped."; continue }
        $channel = $rows[0]
        # Same statements as scripts/setup-buzz-knowledge.py, executed by the
        # database owner because the runtime role cannot write Buzz membership.
        $sql = @"
BEGIN;
WITH owner AS (
  SELECT c.community_id, m.pubkey FROM public.channels c
  JOIN public.channel_members m ON m.channel_id=c.id AND m.community_id=c.community_id
  JOIN public.users u ON u.community_id=c.community_id AND u.pubkey=m.pubkey
  WHERE c.id='$channel' AND m.role::text='owner' AND m.removed_at IS NULL AND u.deactivated_at IS NULL AND u.agent_type IS NULL
  ORDER BY m.joined_at LIMIT 1),
u AS (INSERT INTO public.users(community_id,pubkey,display_name,agent_type,agent_owner_pubkey,about)
  SELECT community_id, decode('$botPub','hex'), 'Knowledge', 'gcor-knowledge', pubkey, 'Governed knowledge proposals and answers. Send !knowledge help.' FROM owner
  ON CONFLICT (community_id,pubkey) DO NOTHING RETURNING 1),
m AS (INSERT INTO public.channel_members(community_id,channel_id,pubkey,role,invited_by)
  SELECT community_id, '$channel', decode('$botPub','hex'), 'bot', pubkey FROM owner
  ON CONFLICT (community_id,channel_id,pubkey) DO NOTHING RETURNING 1)
SELECT (SELECT count(*) FROM owner) AS owners;
INSERT INTO gcor.buzz_knowledge_channels(channel_id) SELECT '$channel' WHERE EXISTS (
  SELECT 1 FROM public.channel_members m JOIN public.users u ON u.community_id=m.community_id AND u.pubkey=m.pubkey
  WHERE m.channel_id='$channel' AND m.role::text='owner' AND m.removed_at IS NULL AND u.agent_type IS NULL)
ON CONFLICT DO NOTHING;
COMMIT;
"@
        $result = Invoke-Psql $sql
        if (($result | Where-Object { $_ -match '^\d+$' } | Select-Object -First 1) -eq '0') { Warn "Channel '$name' has no active human owner; not enrolled."; continue }
        Ok "Channel '$name' ($channel) enrolled; the Knowledge agent replies to !knowledge commands there"
        $agents = Invoke-Psql @"
SELECT coalesce(u.display_name, left(encode(u.pubkey,'hex'),12)),
  CASE WHEN s.pubkey IS NULL THEN 'NO ACTIVE SPONSOR IN CHANNEL - cannot use the brain' ELSE 'ready' END
FROM public.channel_members m JOIN public.users u ON u.community_id=m.community_id AND u.pubkey=m.pubkey
LEFT JOIN public.channel_members s ON s.channel_id=m.channel_id AND s.pubkey=u.agent_owner_pubkey AND s.removed_at IS NULL
WHERE m.channel_id='$channel' AND m.removed_at IS NULL AND u.deactivated_at IS NULL
  AND (u.agent_type IS NOT NULL OR m.role::text='bot') AND u.agent_type IS DISTINCT FROM 'gcor-knowledge'
ORDER BY 1
"@
        foreach ($line in @($agents | Where-Object { $_ })) { $parts = $line -split '\|', 2; Write-Host ("      agent {0,-36} {1}" -f $parts[0], $parts[1]) }
    }

    if (-not $SkipAgentInstall) {
        Step 'Install the agent knowledge protocol into the Buzz nest'
        $node = Get-Command node -ErrorAction SilentlyContinue
        if (-not $node) { throw 'Node.js is not on PATH for this user; agents cannot run gcor-agent.' }
        $nodeMajor = [int]((Invoke-Native node @('--version') -Capture | Select-Object -First 1).TrimStart('v').Split('.')[0])
        if ($nodeMajor -lt 20) { throw "Node $nodeMajor found; gcor-agent needs Node 20+." }
        if (-not (Test-Path $agentCli)) { throw "Missing $agentCli" }
        if (-not (Test-Path $nest)) { throw "Buzz nest not found at $nest" }
        $source = Join-Path $repoRoot 'agent-tools\nest'
        foreach ($dir in @('.agents\skills', '.claude\skills', '.codex\skills')) {
            $target = Join-Path $nest "$dir\gcor-knowledge"
            New-Item -ItemType Directory -Path $target -Force | Out-Null
            Copy-Item (Join-Path $source 'skills\gcor-knowledge\SKILL.md') (Join-Path $target 'SKILL.md') -Force
        }
        Copy-Item (Join-Path $source 'KNOWLEDGE_PROTOCOL.md') (Join-Path $nest 'KNOWLEDGE_PROTOCOL.md') -Force
        $agentsMd = Join-Path $nest 'AGENTS.md'
        if ((Test-Path $agentsMd) -and -not (Select-String -LiteralPath $agentsMd -SimpleMatch '## Knowledge brain protocol' -Quiet)) {
            # Appended below the managed markers, which Buzz regenerates.
            Add-Content -LiteralPath $agentsMd -Value ("`n" + (Get-Content (Join-Path $source 'KNOWLEDGE_PROTOCOL.md') -Raw -Encoding UTF8)) -Encoding utf8
        }
        $claudeMd = Join-Path $nest 'CLAUDE.md'
        if (-not (Test-Path $claudeMd) -or -not (Select-String -LiteralPath $claudeMd -SimpleMatch '@KNOWLEDGE_PROTOCOL.md' -Quiet)) {
            Add-Content -LiteralPath $claudeMd -Value "`n@KNOWLEDGE_PROTOCOL.md" -Encoding utf8
        }
        Ok "Skill, AGENTS.md section and CLAUDE.md pointer installed in $nest"
        if (-not $NoMcpRegistration -and (Get-Command claude -ErrorAction SilentlyContinue)) {
            Invoke-Native claude @('mcp', 'remove', '--scope', 'user', 'gcor-knowledge') -Capture -AllowFailure | Out-Null
            Invoke-Native claude @('mcp', 'add', '--scope', 'user', 'gcor-knowledge', '--', 'node', ($agentCli -replace '\\', '/'), 'mcp') -AllowFailure
            Ok 'Claude Code: gcor-knowledge MCP server registered (user scope)'
        }
    }

    Step 'Verify the signed agent path'
    $ready = Invoke-Native curl.exe @('-s', '-o', 'NUL', '-w', '%{http_code}', 'http://127.0.0.1:5011/health/ready') -Capture -AllowFailure
    if ("$ready" -ne '200') { throw "Enterprise knowledge API not ready (HTTP $ready)" }
    Ok 'gcor-enterprise ready on 127.0.0.1:5011'
    Test-ClockSkew
    # A random, non-member identity must be refused with 403: proves the request
    # reached the new service, the signature verified, and membership is enforced.
    $bytes = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $env:BUZZ_PRIVATE_KEY = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $probe = (& node $agentCli ask --channel 00000000-0000-4000-8000-000000000000 'release probe' 2>&1 | Out-String).Trim() }
    finally { $ErrorActionPreference = $previous; Remove-Item Env:\BUZZ_PRIVATE_KEY -ErrorAction SilentlyContinue }
    if ($probe -match 'Not permitted') { Ok 'Signed agent request verified by gcor-enterprise and refused for a non-member (as designed)' }
    elseif ($probe -match 'signature') { throw "Signature rejected: GCOR_PUBLIC_ORIGIN must equal the URL agents use (http://127.0.0.1:5011). Probe: $probe" }
    else { throw "Unexpected probe result: $probe" }

    Step 'Done'
    Write-Host @"
    Released $Tag (previous: $previousImage).

    Acceptance test in Buzz (#$($ChannelName -join ', #')):
      1. Ask an agent:  @Fizz-Ceo what did we conclude about mesh compute on the Windows build?
         It should run 'gcor-agent ask' first. Nothing is approved yet, so it will investigate.
      2. Ask it to propose the conclusion. It replies to the evidence with !knowledge propose ...
      3. Approve with the command in the Knowledge agent's reply (or the card button).
      4. Ask again in a new thread: the answer should cite the approved document.

    Rollback: .\scripts\release-knowledge-loop.ps1 -Rollback
"@
}
finally { Pop-Location }
