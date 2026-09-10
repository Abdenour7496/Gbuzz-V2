[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $repoRoot
try {
    $candidateFiles = @(
        git ls-files --cached --others --exclude-standard |
            ForEach-Object { $_.Replace('\', '/') }
    )
    if ($LASTEXITCODE -ne 0) { throw "Unable to enumerate release candidate files." }

    $forbidden = @(
        $candidateFiles | Where-Object {
            $_ -match '(^|/)(backups?|archives?)/' -or
            $_ -match '(?i)\.(bak|dump|tar|tgz|zip)$'
        }
    )
    if ($forbidden.Count -gt 0) {
        throw "Generated backup/archive artifacts are part of the release candidate:`n$($forbidden -join "`n")"
    }

    $requiredDockerExclusions = @('backups/', '*.zip', '*.tar', '*.tgz', '*.dump', '*.bak')
    $dockerExclusions = @(Get-Content -LiteralPath .dockerignore | ForEach-Object { $_.Trim() })
    $missing = @($requiredDockerExclusions | Where-Object { $_ -notin $dockerExclusions })
    if ($missing.Count -gt 0) {
        throw "Docker build contexts do not exclude: $($missing -join ', ')"
    }

    Write-Output "Release artifact input gate passed: $($candidateFiles.Count) candidate files; no generated backup/archive artifacts."
}
finally {
    Pop-Location
}
