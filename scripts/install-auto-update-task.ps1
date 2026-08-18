[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = "Gbuzz Stack Auto Update",
    [datetime]$DailyAt = "04:00",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

if ($Uninstall) {
    if ($PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    return
}

$updateScript = Join-Path $PSScriptRoot "update-stack.ps1"
if (-not (Test-Path -LiteralPath $updateScript)) { throw "Missing $updateScript" }

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$updateScript`""
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

if ($PSCmdlet.ShouldProcess($TaskName, "Install daily automatic stack update at $($DailyAt.ToString('HH:mm'))")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Description "Pull, rebuild, apply, and health-check the Gbuzz Docker Compose stack." `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Force | Out-Null
    Write-Host "Installed '$TaskName' for $($DailyAt.ToString('HH:mm')) daily."
}
