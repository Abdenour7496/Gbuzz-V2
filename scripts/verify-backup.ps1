[CmdletBinding()]
param([Parameter(Mandatory)][string]$BackupPath,[byte[]]$Key,[switch]$MetadataOnly,[string]$RepoRoot='C:\Gbuzz',[string]$ConfigPath='C:\Gbuzz\config\windows-startup.compose-files.json',[string]$ExtractTo)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'BackupCrypto.psm1') -Force;Import-Module (Join-Path $PSScriptRoot 'ComposePlan.psm1') -Force
if(-not$Key){$Key=Get-BackupKey};$manifestPath=Join-Path $BackupPath 'manifest.json';if(-not(Test-Path (Join-Path $BackupPath 'COMPLETE'))-or-not(Test-Path $manifestPath)){throw 'Backup is incomplete.'}
$manifest=Get-Content -Raw $manifestPath|ConvertFrom-Json;$artifact=Join-Path $BackupPath $manifest.artifact
if((Get-Item $artifact).Length-ne$manifest.artifact_size){throw 'Encrypted artifact size mismatch.'};if((Get-FileSha256 $artifact)-ne$manifest.artifact_sha256){throw 'Encrypted artifact hash mismatch.'}
$unsigned=[ordered]@{format=$manifest.format;run_id=$manifest.run_id;created_at=$manifest.created_at;cipher=$manifest.cipher;artifact=$manifest.artifact;artifact_size=$manifest.artifact_size;artifact_sha256=$manifest.artifact_sha256;artifact_hmac_sha256=$manifest.artifact_hmac_sha256};if((Get-ManifestMac ($unsigned|ConvertTo-Json -Compress) $Key)-ne$manifest.manifest_hmac_sha256){throw 'Recovery manifest authentication failed.'}
if(-not$MetadataOnly){
    $plan=Get-GbuzzComposePlan $RepoRoot $ConfigPath;$temp=Join-Path ([IO.Path]::GetTempPath()) ('gbuzz-verify-'+[Guid]::NewGuid().ToString('N'));New-Item -ItemType Directory $temp|Out-Null
    try{$tar=Join-Path $temp 'payload.tar';Unprotect-BackupFile $artifact $tar $Key;if($ExtractTo){New-Item -ItemType Directory -Force $ExtractTo|Out-Null};$mountTarget=if($ExtractTo){$ExtractTo}else{$temp};Push-Location $RepoRoot;try{& docker compose @($plan.Arguments) run --rm --no-deps -T --volume "${temp}:/verify" --volume "${mountTarget}:/verify-output" --volume "${PSScriptRoot}:/backup-tools:ro" --entrypoint python gcor-proxy /backup-tools/backup-evidence.py /verify/payload.tar --extract-to /verify-output;if($LASTEXITCODE-ne 0){throw 'Inner evidence verification failed.'}}finally{Pop-Location}}finally{if(-not$ExtractTo){Remove-Item -LiteralPath $temp -Recurse -Force}else{Remove-Item -LiteralPath (Join-Path $temp 'payload.tar') -Force -ErrorAction SilentlyContinue;Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue}}
}
Write-Output "Backup verified: $($manifest.run_id)"
