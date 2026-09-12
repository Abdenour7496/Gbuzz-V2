[CmdletBinding(SupportsShouldProcess)]
param([string]$BackupRoot='C:\ProgramData\Gbuzz\encrypted-backups',[string]$MetricsRoot='C:\ProgramData\Gbuzz\backup-metrics',[string]$AuditRoot='C:\ProgramData\Gbuzz\startup-audit')
$ErrorActionPreference='Stop';$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name
foreach($path in @($BackupRoot,$MetricsRoot,$AuditRoot)){
    if($PSCmdlet.ShouldProcess($path,'Create protected Gbuzz operations directory')){
        New-Item -ItemType Directory -Force $path|Out-Null;$acl=Get-Acl $path;$acl.SetAccessRuleProtection($true,$false)
        foreach($identity in @($user,'BUILTIN\Administrators','NT AUTHORITY\SYSTEM')){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
        Set-Acl -LiteralPath $path -AclObject $acl
    }
}
