param(
    [string]$BaseUrl = "http://127.0.0.1:5001",
    [string]$ChannelId = "chan-lifecycle-smoke",
    [string]$ChannelName = "lifecycle-smoke",
    [string]$ApprovedBy = "npub1admin",
    [string]$ApiSecret = "",
    [switch]$SkipHealthCheck,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ApiSecret) {
    if ($env:STACK_API_SECRET) {
        $ApiSecret = $env:STACK_API_SECRET
    }
    elseif ($env:INGEST_WEBHOOK_SECRET) {
        $ApiSecret = $env:INGEST_WEBHOOK_SECRET
    }
    else {
        $envFile = Join-Path $PSScriptRoot "..\.env"
        if (Test-Path $envFile) {
            $line = Get-Content $envFile | Where-Object { $_ -match '^STACK_API_SECRET=' } | Select-Object -First 1
            if (-not $line) {
                $line = Get-Content $envFile | Where-Object { $_ -match '^INGEST_WEBHOOK_SECRET=' } | Select-Object -First 1
            }
            if ($line) {
                $ApiSecret = ($line -split '=', 2)[1]
            }
        }
    }
}

if (-not $ApiSecret) {
    throw "ApiSecret is required. Set STACK_API_SECRET/INGEST_WEBHOOK_SECRET or pass -ApiSecret."
}

function Invoke-GcorPost {
    param(
        [string]$Path,
        [hashtable]$Body
    )

    $json = $Body | ConvertTo-Json -Compress -Depth 10
    return Invoke-RestMethod -Method Post -Uri "$BaseUrl$Path" -ContentType "application/json" -Headers @{ "X-Gcor-Webhook-Secret" = $ApiSecret } -Body $json
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Write-Step {
    param([string]$Message)

    if (-not $Json) {
        Write-Output $Message
    }
}

$oldSource = $null
$newSource = $null
$beforeSources = @()
$afterSources = @()
$statusSummary = @{}

try {
    if (-not $SkipHealthCheck) {
        $health = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health"
        Assert-True ($health.status -eq "ok") "Health check failed at $BaseUrl/health"
    }

    $suffix = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $query = "lifecycle smoke ranking phrase $suffix"
    $oldSource = "buzz://lifecycle-old-$suffix"
    $newSource = "buzz://lifecycle-new-$suffix"

    Write-Step "[1/8] Promote old knowledge"
    $null = Invoke-GcorPost -Path "/api/knowledge/promote" -Body @{
        title = "Lifecycle Old"
        content = "$query old"
        source_uri = $oldSource
        channel_id = $ChannelId
        channel_name = $ChannelName
        access_level = "public"
    }

    Write-Step "[2/8] Approve old knowledge"
    $oldApproved = Invoke-GcorPost -Path "/api/knowledge/approve" -Body @{
        target_source_uri = $oldSource
        approved_by = $ApprovedBy
        note = "smoke approve old"
    }
    Assert-True ($oldApproved.knowledge_state -eq "approved") "Old source was not approved"

    Write-Step "[3/8] Promote new knowledge"
    $null = Invoke-GcorPost -Path "/api/knowledge/promote" -Body @{
        title = "Lifecycle New"
        content = "$query new"
        source_uri = $newSource
        channel_id = $ChannelId
        channel_name = $ChannelName
        access_level = "public"
    }

    Write-Step "[4/8] Approve new knowledge"
    $newApproved = Invoke-GcorPost -Path "/api/knowledge/approve" -Body @{
        target_source_uri = $newSource
        approved_by = $ApprovedBy
        note = "smoke approve new"
    }
    Assert-True ($newApproved.knowledge_state -eq "approved") "New source was not approved"

    Write-Step "[5/8] Supersede old with new"
    $superseded = Invoke-GcorPost -Path "/api/knowledge/transition" -Body @{
        transition = "superseded"
        target_source_uri = $oldSource
        superseded_by_source_uri = $newSource
        changed_by = $ApprovedBy
        note = "smoke supersede"
    }
    Assert-True ($superseded.knowledge_state -eq "superseded") "Old source was not transitioned to superseded"

    Write-Step "[6/8] Ask with approved_only + recent preference"
    $askBeforeArchive = Invoke-GcorPost -Path "/api/ask" -Body @{
        query = $query
        top_k = 6
        hops = 0
        access_level = "public"
        channel_id = $ChannelId
        channel_name = $ChannelName
        approved_only = $true
        prefer_recent_approved = $true
    }

    $beforeSources = @($askBeforeArchive.citations | ForEach-Object { $_.source_uri })
    Assert-True ($beforeSources -contains $newSource) "Expected new source not found in ask results before archive"

    Write-Step "[7/8] Archive old"
    $archived = Invoke-GcorPost -Path "/api/knowledge/transition" -Body @{
        transition = "archived"
        target_source_uri = $oldSource
        changed_by = $ApprovedBy
        note = "smoke archive"
    }
    Assert-True ($archived.knowledge_state -eq "archived") "Old source was not transitioned to archived"

    Write-Step "[8/8] Ask again and verify archived exclusion"
    $askAfterArchive = Invoke-GcorPost -Path "/api/ask" -Body @{
        query = $query
        top_k = 6
        hops = 0
        access_level = "public"
        channel_id = $ChannelId
        channel_name = $ChannelName
        approved_only = $true
        prefer_recent_approved = $true
    }

    $afterSources = @($askAfterArchive.citations | ForEach-Object { $_.source_uri })
    Assert-True (-not ($afterSources -contains $oldSource)) "Archived source should not appear in approved_only ask results"
    Assert-True ($afterSources -contains $newSource) "Expected new source missing after archive"

    $statusSummary = @{
        passed = $true
        base_url = $BaseUrl
        channel_id = $ChannelId
        channel_name = $ChannelName
        old_source = $oldSource
        new_source = $newSource
        results_before_archive = $beforeSources
        results_after_archive = $afterSources
    }

    if ($Json) {
        $statusSummary | ConvertTo-Json -Compress -Depth 8
    }
    else {
        Write-Output ""
        Write-Output "Smoke test passed"
        Write-Output "BaseUrl: $BaseUrl"
        Write-Output "Channel: $ChannelName ($ChannelId)"
        Write-Output "Old source: $oldSource"
        Write-Output "New source: $newSource"
        Write-Output "Results before archive: $($beforeSources -join ', ')"
        Write-Output "Results after archive:  $($afterSources -join ', ')"
    }
}
catch {
    if ($Json) {
        @{
            passed = $false
            error = $_.Exception.Message
            base_url = $BaseUrl
            channel_id = $ChannelId
            channel_name = $ChannelName
            old_source = $oldSource
            new_source = $newSource
            results_before_archive = $beforeSources
            results_after_archive = $afterSources
        } | ConvertTo-Json -Compress -Depth 8
    }
    else {
        Write-Error "Knowledge smoke test failed: $($_.Exception.Message)"
    }
    exit 1
}