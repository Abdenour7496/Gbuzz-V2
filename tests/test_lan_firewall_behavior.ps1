$ErrorActionPreference='Stop';$root=Split-Path -Parent $PSScriptRoot;$script=Join-Path $root 'scripts\configure-gbuzz-lan-boundary.ps1'
function Throws([scriptblock]$Action,[string]$Message){$ok=$false;try{&$Action}catch{if($_.Exception.Message-notmatch[regex]::Escape($Message)){throw};$ok=$true};if(-not$ok){throw "Expected failure containing: $Message"}}
function Rule([string]$Description='prior',[string]$Direction='Inbound',[string]$Action='Block',[string]$Enabled='True',[string]$Profile='Private',[string]$Protocol='TCP',[string]$LocalPort='3000',[string]$RemotePort='Any',[string[]]$LocalAddress=@('192.168.1.10'),[string[]]$RemoteAddress=@('192.168.1.0/24')){[pscustomobject]@{DisplayName='Gbuzz Relay - approved LAN only';Description=$Description;Direction=$Direction;Action=$Action;Enabled=$Enabled;Profile=$Profile;PolicyStoreSourceType='Local';Protocol=$Protocol;LocalPort=$LocalPort;RemotePort=$RemotePort;LocalAddress=$LocalAddress;RemoteAddress=$RemoteAddress}}
$global:fwRules=@();$global:restoreMutation=$null
function global:Get-NetIPAddress {[pscustomobject]@{IPAddress='192.168.1.10'}}
function global:Get-NetFirewallRule { @($global:fwRules) }
function global:Get-NetFirewallPortFilter { process { [pscustomobject]@{Protocol=$_.Protocol;LocalPort=$_.LocalPort;RemotePort=$_.RemotePort} } }
function global:Get-NetFirewallAddressFilter { process { [pscustomobject]@{LocalAddress=$_.LocalAddress;RemoteAddress=$_.RemoteAddress} } }
function global:Get-NetNatStaticMapping { @() }
function global:Get-Process { @() }
function global:netsh { @() }
function global:Remove-NetFirewallRule { process {$global:fwRules=@($global:fwRules|Where-Object{$_-ne$_})} }
function global:New-NetFirewallRule {
  param($DisplayName,$Description,$Direction,$Action,$Enabled='True',$Profile,$Protocol,$LocalPort,$RemotePort='Any',$LocalAddress,$RemoteAddress)
  $r=Rule -Description $Description -Direction $Direction -Action $Action -Enabled $Enabled -Profile $Profile -Protocol $Protocol -LocalPort $LocalPort -RemotePort $RemotePort -LocalAddress @($LocalAddress) -RemoteAddress @($RemoteAddress)
  if($global:restoreMutation){$r.($global:restoreMutation.field)=$global:restoreMutation.value};$global:fwRules+=,$r;$r
}
try{
  $tmp=Join-Path ([IO.Path]::GetTempPath()) ('gbuzz-fw-'+[guid]::NewGuid().ToString('n'));New-Item -ItemType Directory $tmp|Out-Null
  $prior=Rule;$snapshot=@([ordered]@{display_name=$prior.DisplayName;description=$prior.Description;direction=$prior.Direction;action=$prior.Action;enabled=$prior.Enabled;profile=@($prior.Profile);protocol=$prior.Protocol;local_port=@($prior.LocalPort);remote_port=@($prior.RemotePort);local_address=@($prior.LocalAddress);remote_address=@($prior.RemoteAddress)});$snapshot|ConvertTo-Json -Depth 6|Set-Content "$tmp/prior.json"
  # Explicit rollback restores the complete prior rule and verifies final state.
  $global:fwRules=@(Rule -Description 'managed' -Action 'Allow');& $script -Mode Rollback -BindAddress 192.168.1.10 -RemoteCidr 192.168.1.0/24 -ExpectedRollbackSnapshot "$tmp/prior.json" -Confirm:$false
  if($global:fwRules.Count-ne1-or$global:fwRules[0].Description-ne'prior'-or$global:fwRules[0].Action-ne'Block'){throw 'Explicit rollback final state mismatch'}
  # A field mutation during restore fails critically after comparing final state.
  $global:fwRules=@(Rule -Description 'managed' -Action 'Allow');$global:restoreMutation=@{field='RemoteAddress';value=@('Any')};Throws {& $script -Mode Rollback -BindAddress 192.168.1.10 -RemoteCidr 192.168.1.0/24 -ExpectedRollbackSnapshot "$tmp/prior.json" -Confirm:$false} 'explicit rollback verification failed';$global:restoreMutation=$null
  if($global:fwRules[0].RemoteAddress-ne'Any'){throw 'Injected partial restore final state was not observed'}
  # Missing/contradictory effective-path evidence remains non-compliant despite a locally compliant rule.
  $global:fwRules=@(Rule -Action 'Allow' -Description '');$audit=& $script -Mode Audit -BindAddress 192.168.1.10 -RemoteCidr 192.168.1.0/24|ConvertFrom-Json;if($audit.compliant-ne$false-or$audit.effective_path_and_routed_client_proof-ne$false){throw 'Missing routed evidence did not fail closed'}
}finally{Remove-Item function:\Get-NetIPAddress,function:\Get-NetFirewallRule,function:\Get-NetFirewallPortFilter,function:\Get-NetFirewallAddressFilter,function:\Get-NetNatStaticMapping,function:\Get-Process,function:\netsh,function:\Remove-NetFirewallRule,function:\New-NetFirewallRule -ErrorAction SilentlyContinue;if($tmp){Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue}}
Write-Output 'LAN firewall behavioral negatives passed.'
