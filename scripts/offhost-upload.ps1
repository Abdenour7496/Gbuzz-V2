[CmdletBinding()]
param([Parameter(Mandatory)][string]$BackupPath,[ValidateSet('AzureBlob','S3','Filesystem')][string]$Provider,[switch]$Approved)
$ErrorActionPreference='Stop'
if(-not $Approved){throw 'Off-host transfer is disabled until the owner supplies and approves a destination and credentials.'}
& (Join-Path $PSScriptRoot 'verify-backup.ps1') -BackupPath $BackupPath
switch($Provider){
    AzureBlob {if(-not $env:GBUZZ_BACKUP_AZURE_DESTINATION){throw 'GBUZZ_BACKUP_AZURE_DESTINATION is required.'};az storage blob upload-batch --auth-mode login --overwrite false --destination $env:GBUZZ_BACKUP_AZURE_DESTINATION --source $BackupPath}
    S3 {if(-not $env:GBUZZ_BACKUP_S3_DESTINATION){throw 'GBUZZ_BACKUP_S3_DESTINATION is required.'};aws s3 cp $BackupPath $env:GBUZZ_BACKUP_S3_DESTINATION --recursive --no-progress}
    Filesystem {if(-not $env:GBUZZ_BACKUP_FILESYSTEM_DESTINATION){throw 'GBUZZ_BACKUP_FILESYSTEM_DESTINATION is required.'};Copy-Item -LiteralPath $BackupPath -Destination $env:GBUZZ_BACKUP_FILESYSTEM_DESTINATION -Recurse}
}
