# Read-only: captures compose state and logs of non-healthy services to _diag\stack-diag.txt
$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent $PSScriptRoot
$out = Join-Path $repoRoot '_diag\stack-diag.txt'
Push-Location $repoRoot
Import-Module (Join-Path $PSScriptRoot 'ComposePlan.psm1') -Force
$plan = Get-GbuzzComposePlan $repoRoot (Join-Path $repoRoot 'config/windows-startup.compose-files.json')
$ca = @('compose') + $plan.Arguments
function Run([string]$title, [string[]]$a) { "`n===== $title =====" | Out-File $out -Append -Encoding utf8; & docker @a 2>&1 | ForEach-Object { "$_" } | Out-File $out -Append -Encoding utf8 }
"diag $(Get-Date -Format o)" | Out-File $out -Encoding utf8
Run 'ps -a' ($ca + @('ps', '-a', '--format', 'table {{.Service}}\t{{.Image}}\t{{.State}}\t{{.Status}}'))
$rows = & docker @ca ps -a --format json 2>$null | ForEach-Object { $_ | ConvertFrom-Json }
foreach ($r in $rows) {
    if ($r.Health -ne 'healthy' -and -not ($r.State -eq 'exited' -and $r.ExitCode -eq 0) -or $r.Service -eq 'relay') {
        Run "logs $($r.Service) (state=$($r.State) health=$($r.Health) exit=$($r.ExitCode))" ($ca + @('logs', '--tail', '80', '--no-color', $r.Service))
    }
}
Run 'images relay' @('image', 'ls', 'ghcr.io/block/buzz')
"`nDONE" | Out-File $out -Append -Encoding utf8
Pop-Location
