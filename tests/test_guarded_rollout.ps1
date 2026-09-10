$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('gbuzz-rollout-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $testRoot 'scripts') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceRoot 'scripts/deploy-gcor-safeguards.ps1') -Destination (Join-Path $testRoot 'scripts')

# Simulate Docker outcomes; never invoke the real engine in this test.
function docker {
    $arguments = @($args)
    $global:LASTEXITCODE = 0
    if ($arguments -contains 'config') { return }
    if ($arguments -contains 'ps') { if ($global:gbuzzRolloutTestScenario -ne 'missing') { 'proxy-id' }; return }
    if ($arguments[0] -eq 'inspect') {
        if ($arguments[2] -eq '{{.Image}}') { 'sha256:' + ('a' * 64) }
        else { Join-Path $testRoot 'docker-compose.yml' }
        return
    }
    if ($arguments[0] -eq 'build' -and $global:gbuzzRolloutTestScenario -eq 'build-failure') { $global:LASTEXITCODE = 1; return }
    if ($arguments[0] -eq 'exec' -and $arguments[-1] -like '*to_regclass*' -and $global:gbuzzRolloutTestScenario -eq 'missing-migration') {
        $global:LASTEXITCODE = 1; return
    }
    if ($arguments -contains 'up') {
        $global:gbuzzRolloutTestRecreations++
        if ($global:gbuzzRolloutTestScenario -eq 'rollout-failure' -and $global:gbuzzRolloutTestRecreations -eq 1) {
            $global:LASTEXITCODE = 1
        }
        return
    }
}

try {
    foreach ($global:gbuzzRolloutTestScenario in @('missing', 'missing-migration', 'success', 'build-failure', 'rollout-failure')) {
        $global:gbuzzRolloutTestRecreations = 0
        $failure = $null
        try { & (Join-Path $testRoot 'scripts/deploy-gcor-safeguards.ps1') -CheckOnly:($global:gbuzzRolloutTestScenario -eq 'missing') }
        catch { $failure = $_.Exception.Message }
        switch ($global:gbuzzRolloutTestScenario) {
            'missing' { if ($failure -or $global:gbuzzRolloutTestRecreations) { throw 'Missing-proxy safety check failed' } }
            'missing-migration' { if ($failure -notlike 'Governance migration 0007*' -or $global:gbuzzRolloutTestRecreations) { throw 'Missing migration changed the stack' } }
            'success' { if ($failure -or $global:gbuzzRolloutTestRecreations -ne 1) { throw "Success path failed: $failure" } }
            'build-failure' { if ($failure -notlike 'Build failed*' -or $global:gbuzzRolloutTestRecreations) { throw 'Build failure changed the stack' } }
            'rollout-failure' {
                if ($failure -notlike '*Previous proxy image restored and healthy*' -or $global:gbuzzRolloutTestRecreations -ne 2) { throw "Rollback failed: $failure" }
                $overlay = Get-Content -Raw -LiteralPath (Join-Path $testRoot 'docker-compose.safeguards.override.json') | ConvertFrom-Json
                if ($overlay.services.'gcor-proxy'.image -notlike 'gbuzz-gcor-rollback:*') { throw 'Rollback image was not retained in overlay' }
            }
        }
    }
    Write-Output 'Guarded rollout tests passed: missing proxy, missing migration, success, build failure, automatic rollback.'
}
finally {
    # Only remove the unique directory created by this test under the OS temp directory.
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolvedTestRoot.StartsWith($tempRoot) -or (Split-Path $resolvedTestRoot -Leaf) -notlike 'gbuzz-rollout-test-*') {
        throw 'Refusing cleanup outside the rollout test directory'
    }
    Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
}

