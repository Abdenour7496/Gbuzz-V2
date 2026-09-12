[CmdletBinding()]
param([Parameter(Mandatory)][string]$BackupPath,[string]$RepoRoot='C:\Gbuzz',[string]$ConfigPath='C:\Gbuzz\config\windows-startup.compose-files.json')
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'BackupCrypto.psm1') -Force
$key=Get-BackupKey
$temp=Join-Path ([IO.Path]::GetTempPath()) ('gbuzz-sample-'+[Guid]::NewGuid().ToString('N'));$container='gbuzz-sample-pg-'+[Guid]::NewGuid().ToString('N').Substring(0,12)
try{
    New-Item -ItemType Directory $temp|Out-Null;$manifest=Get-Content -Raw (Join-Path $BackupPath 'manifest.json')|ConvertFrom-Json;& (Join-Path $PSScriptRoot 'verify-backup.ps1') -BackupPath $BackupPath -Key $key -RepoRoot $RepoRoot -ConfigPath $ConfigPath -ExtractTo $temp
    $oldPassword=$env:POSTGRES_PASSWORD;try{$env:POSTGRES_PASSWORD=[Guid]::NewGuid().ToString('N');docker run -d --name $container -e POSTGRES_USER=drill -e POSTGRES_PASSWORD -e POSTGRES_DB=drill pgvector/pgvector:pg17|Out-Null}finally{$env:POSTGRES_PASSWORD=$oldPassword};if($LASTEXITCODE -ne 0){throw 'Unable to start isolated PostgreSQL.'}
    $ready=$false;for($i=0;$i -lt 30;$i++){docker exec $container pg_isready -U drill -d drill *> $null;if($LASTEXITCODE -eq 0){$ready=$true;break};Start-Sleep 1};if(-not $ready){throw 'Isolated PostgreSQL readiness timed out.'}
    docker cp (Join-Path $temp 'postgres.dump') "${container}:/tmp/postgres.dump"|Out-Null;docker exec $container pg_restore --exit-on-error --no-owner --no-privileges -U drill -d drill /tmp/postgres.dump|Out-Null;if($LASTEXITCODE -ne 0){throw 'PostgreSQL sample restore failed.'}
    $counts=docker exec $container psql -U drill -d drill -Atc "SELECT (SELECT count(*) FROM public.events)||'|'||(SELECT count(*) FROM gcor.knowledge_entries)";if($LASTEXITCODE -ne 0){throw 'Restored PostgreSQL relay/GCOR validation failed.'};$parts=$counts-split'\|';if($parts.Count-ne 2-or[int64]$parts[0]-lt 0-or[int64]$parts[1]-lt 0){throw 'Restored PostgreSQL table counts are invalid.'}
    $inventory=Get-Content -Raw (Join-Path $temp 'minio\inventory.json')|ConvertFrom-Json;$samples=@();foreach($bucket in $inventory.buckets){$sample=$inventory.versions|Where-Object{$_.bucket-eq$bucket-and-not$_.delete_marker}|Select-Object -First 1;if($sample){if((Get-FileSha256 (Join-Path $temp "minio\blobs\$($sample.sha256)"))-ne$sample.sha256){throw "MinIO sample checksum failed for bucket $bucket"};$samples+=[ordered]@{bucket=$bucket;key=$sample.key;version=$sample.version_id}}};$deleteMarkers=@($inventory.versions|Where-Object{$_.delete_marker}).Count
    tar -tf (Join-Path $temp 'relay-git.tar') *> $null;if($LASTEXITCODE -ne 0){throw 'Relay Git archive validation failed.'}
    [ordered]@{passed=$true;tested_at=[DateTimeOffset]::UtcNow.ToString('o');backup_run=$manifest.run_id;relay_events=[int64]$parts[0];knowledge_entries=[int64]$parts[1];buckets_sampled=$samples;delete_markers_preserved=$deleteMarkers}|ConvertTo-Json -Depth 5
} finally {docker rm -f -v $container 2>$null|Out-Null;Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue}
