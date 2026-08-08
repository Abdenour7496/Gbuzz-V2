[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidAssignmentToAutomaticVariable', 'args', Justification = 'False positive in current analyzer diagnostics for this script.')]
param(
    [string]$BaseUrl = "http://127.0.0.1:5001",
    [string]$ApprovedBy = "npub1admin",
    [string]$ChannelPrefix = "lifecycle-gate",
    [string]$ApiSecret = "",
    [switch]$SkipHealthCheck,
    [switch]$AllowExtraCitations,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

$runId = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$channelId = "chan-$ChannelPrefix-$runId"
$channelName = "$ChannelPrefix-$runId"
$smokeScript = Join-Path $PSScriptRoot "gcor-knowledge-smoke.ps1"

try {
    if ($SkipHealthCheck) {
        if ($ApiSecret) {
            $raw = & powershell -ExecutionPolicy Bypass -File $smokeScript -BaseUrl $BaseUrl -ChannelId $channelId -ChannelName $channelName -ApprovedBy $ApprovedBy -ApiSecret $ApiSecret -Json -SkipHealthCheck 2>&1
        }
        else {
            $raw = & powershell -ExecutionPolicy Bypass -File $smokeScript -BaseUrl $BaseUrl -ChannelId $channelId -ChannelName $channelName -ApprovedBy $ApprovedBy -Json -SkipHealthCheck 2>&1
        }
    }
    else {
        if ($ApiSecret) {
            $raw = & powershell -ExecutionPolicy Bypass -File $smokeScript -BaseUrl $BaseUrl -ChannelId $channelId -ChannelName $channelName -ApprovedBy $ApprovedBy -ApiSecret $ApiSecret -Json 2>&1
        }
        else {
            $raw = & powershell -ExecutionPolicy Bypass -File $smokeScript -BaseUrl $BaseUrl -ChannelId $channelId -ChannelName $channelName -ApprovedBy $ApprovedBy -Json 2>&1
        }
    }
    $exitCode = $LASTEXITCODE
    $rawText = ($raw | ForEach-Object { $_.ToString() }) -join "`n"

    $smoke = $rawText | ConvertFrom-Json

    Assert-True ($exitCode -eq 0) "Smoke script exited with code $exitCode"
    Assert-True ([bool]$smoke.passed) "Smoke script returned passed=false"

    $before = @($smoke.results_before_archive)
    $after = @($smoke.results_after_archive)
    $newSource = [string]$smoke.new_source
    $oldSource = [string]$smoke.old_source

    Assert-True (($before -contains $newSource)) "New source missing before archive"
    Assert-True (($after -contains $newSource)) "New source missing after archive"
    Assert-True (-not ($after -contains $oldSource)) "Old source appears after archive"

    if (-not $AllowExtraCitations) {
        Assert-True (($before.Count -eq 1) -and ($before[0] -eq $newSource)) "Expected exactly one citation before archive and it must be the new source"
        Assert-True (($after.Count -eq 1) -and ($after[0] -eq $newSource)) "Expected exactly one citation after archive and it must be the new source"
    }

    $result = @{
        passed = $true
        mode = if ($AllowExtraCitations) { "lenient" } else { "strict" }
        run_id = $runId
        base_url = $BaseUrl
        channel_id = $channelId
        channel_name = $channelName
        checks = @{
            includes_new_before_archive = $true
            includes_new_after_archive = $true
            excludes_old_after_archive = $true
            exact_citation_sets = -not $AllowExtraCitations
        }
        smoke = $smoke
    }

    if ($Json) {
        $result | ConvertTo-Json -Compress -Depth 10
    }
    else {
        Write-Output "Knowledge regression gate passed"
        Write-Output "Mode: $($result.mode)"
        Write-Output "Channel: $channelName ($channelId)"
        Write-Output "New source: $newSource"
        Write-Output "Old source: $oldSource"
        Write-Output "Before archive: $($before -join ', ')"
        Write-Output "After archive:  $($after -join ', ')"
    }
}
catch {
    $failure = @{
        passed = $false
        mode = if ($AllowExtraCitations) { "lenient" } else { "strict" }
        run_id = $runId
        base_url = $BaseUrl
        channel_id = $channelId
        channel_name = $channelName
        error = $_.Exception.Message
    }

    if ($Json) {
        $failure | ConvertTo-Json -Compress -Depth 10
    }
    else {
        Write-Error "Knowledge regression gate failed: $($_.Exception.Message)"
    }
    exit 1
}
