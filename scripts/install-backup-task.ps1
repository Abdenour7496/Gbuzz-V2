[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName='Gbuzz-Encrypted-Backup',[string]$ScriptPath='C:\Gbuzz\scripts\backup-stack.ps1',[int]$IntervalHours=6)
$ErrorActionPreference='Stop'
if(-not(Test-Path $ScriptPath)){throw "Backup script not found: $ScriptPath"}
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$action=New-ScheduledTaskAction -Execute (Get-Command powershell.exe -ErrorAction Stop).Source -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptPath`""
$trigger=New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4) -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 15) -StartWhenAvailable
if($PSCmdlet.ShouldProcess($TaskName,'Register encrypted backup task')){Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force|Out-Null}
