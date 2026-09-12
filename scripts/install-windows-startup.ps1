[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName='Gbuzz-Delayed-Startup',[string]$ScriptPath='C:\Gbuzz\scripts\start-gbuzz-at-logon.ps1',[int]$DelaySeconds=60)
$ErrorActionPreference='Stop'
if(-not(Test-Path $ScriptPath)){throw "Startup script not found: $ScriptPath"}
$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$action=New-ScheduledTaskAction -Execute (Get-Command powershell.exe -ErrorAction Stop).Source -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$ScriptPath`""
$trigger=New-ScheduledTaskTrigger -AtLogOn -User $user;$trigger.Delay="PT${DelaySeconds}S"
$principal=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)
if($PSCmdlet.ShouldProcess($TaskName,'Register least-privilege delayed logon startup task')){Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force|Out-Null}
