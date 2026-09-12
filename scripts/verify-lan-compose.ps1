[CmdletBinding()]
param([string]$BindAddress=$env:BUZZ_LAN_BIND_ADDR,[string]$RemoteCidr=$env:BUZZ_LAN_REMOTE_CIDR,[int]$Port=3000)
$ErrorActionPreference='Stop';Import-Module (Join-Path $PSScriptRoot LanSecurity.psm1) -Force
if(-not$BindAddress-or-not$RemoteCidr){throw 'BUZZ_LAN_BIND_ADDR and BUZZ_LAN_REMOTE_CIDR are required.'}
if(-not(Test-GbuzzPrivateIPv4 $BindAddress)-or-not(Test-GbuzzAddressInCidr $BindAddress $RemoteCidr)){throw 'LAN validation failed.'}
$env:BUZZ_LAN_BIND_ADDR=$BindAddress;$env:BUZZ_HTTP_PORT=[string]$Port
$j=docker compose -f docker-compose.yml -f docker-compose.lan.yml config --format json|ConvertFrom-Json;if($LASTEXITCODE-ne0){throw 'Compose LAN plan failed.'};$ports=@($j.services.relay.ports);if($ports.Count-ne1){throw 'Relay must publish exactly one port.'};$b=$ports[0];if($b.host_ip-ne$BindAddress-or$b.published-ne[string]$Port-or$b.target-ne3000){throw 'Resolved publication differs from approved binding.'};Write-Output "Verified LAN relay ${BindAddress}:$Port for $RemoteCidr."
