[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Run this drill with PowerShell 7 (pwsh).' }
$root=Split-Path -Parent $PSScriptRoot
Push-Location $root
$stamp=[Guid]::NewGuid().ToString('N')
$pgName="gbuzz-drill-pg-$stamp"
$s3Name="gbuzz-drill-s3-$stamp"
$secret=[Guid]::NewGuid().ToString('N')
$started=Get-Date
$folder=Join-Path $root "backups/restore-drill-$stamp"
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw 'Restore drill command failed; live services were not changed.' } }
try {
    $config=docker compose config --format json | ConvertFrom-Json;Check-Exit
    $network=$config.networks.'buzz-net'.name
    if (-not $network) { throw 'Unable to resolve the stack network' }
    New-Item -ItemType Directory -Path $folder | Out-Null
    docker compose exec -T postgres pg_dump -U $config.services.postgres.environment.POSTGRES_USER -d $config.services.postgres.environment.POSTGRES_DB -Fc -f "/tmp/$stamp.dump";Check-Exit
    docker compose cp "postgres:/tmp/$stamp.dump" "$folder/postgres.dump";Check-Exit
    docker run -d --name $pgName --network $network -e POSTGRES_USER=drill -e "POSTGRES_PASSWORD=$secret" -e POSTGRES_DB=drill $config.services.postgres.image | Out-Null;Check-Exit
    docker run -d --name $s3Name --network $network -e MINIO_ROOT_USER=drill-admin -e "MINIO_ROOT_PASSWORD=$secret" $config.services.minio.image server /data | Out-Null;Check-Exit
    $ready=$false
    for($i=0;$i -lt 30;$i++) { docker exec $pgName pg_isready -U drill -d drill *> $null; if($LASTEXITCODE -eq 0){$ready=$true;break};Start-Sleep -Seconds 1 }
    if(-not $ready){throw 'Isolated PostgreSQL failed readiness'}
    docker cp "$folder/postgres.dump" "${pgName}:/tmp/restore.dump";Check-Exit
    docker exec $pgName pg_restore --exit-on-error --no-owner --no-privileges -U drill -d drill /tmp/restore.dump;Check-Exit
    docker compose run --rm --no-deps --volume "${folder}:/backup" --volume "${PSScriptRoot}:/tools:ro" `
        -e PYTHONPATH=/app -e "DRILL_POSTGRES_HOST=$pgName" -e "DRILL_S3_HOST=$s3Name" -e "DRILL_SECRET=$secret" `
        gcor-proxy python /tools/restore-drill.py;Check-Exit
    $report=Get-Content -Raw -LiteralPath "$folder/report.json" | ConvertFrom-Json
    $report | Add-Member -NotePropertyName database_dump_sha256 -NotePropertyValue (Get-FileHash -Algorithm SHA256 -LiteralPath "$folder/postgres.dump").Hash
    $report | Add-Member -NotePropertyName elapsed_seconds -NotePropertyValue ([int]((Get-Date)-$started).TotalSeconds)
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$folder/report.json"
    Write-Output "Restore drill passed. Evidence: $folder/report.json"
} finally {
    # Names are fixed-prefix UUIDs generated above, never inferred from user paths.
    docker rm -f -v $pgName $s3Name 2>$null | Out-Null
    docker compose exec -T postgres rm -f "/tmp/$stamp.dump" 2>$null | Out-Null
    Pop-Location
}
