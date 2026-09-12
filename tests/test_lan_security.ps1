$ErrorActionPreference='Stop';$root=Split-Path -Parent $PSScriptRoot;Import-Module (Join-Path $root 'scripts\LanSecurity.psm1') -Force
function Throws([scriptblock]$Action,[string]$Message){$ok=$false;try{&$Action}catch{if($_.Exception.Message-notmatch[regex]::Escape($Message)){throw};$ok=$true};if(-not$ok){throw "Expected failure containing: $Message"}}
foreach($a in @('10.1.2.3','172.31.2.3','192.168.20.4')){if(-not(Test-GbuzzPrivateIPv4 $a)){throw "Private rejected: $a"}};foreach($a in @('0.0.0.0','127.0.0.1','8.8.8.8')){if(Test-GbuzzPrivateIPv4 $a){throw "Unsafe accepted: $a"}}
$acl=Get-Content -Raw (Join-Path $root 'scripts\protect-gbuzz-secrets.ps1');foreach($token in @('ProtectedConfiguration','ExpectedRunId','Hard-linked secret path rejected','Reparse-point path component rejected','Rollback MAC key identity mismatch','Target $EvidenceRoot')){if($acl-notmatch[regex]::Escape($token)){throw "ACL control missing $token"}}
$fw=Get-Content -Raw (Join-Path $root 'scripts\configure-gbuzz-lan-boundary.ps1');foreach($token in @('Assert-Snapshot','ExpectedRollbackSnapshot','effective_path_and_routed_client_proof','compliant=$ok-and-not$candidateRules.Count-and-not$nat.Count-and-not$portproxy.Count-and$proofOk')){if($fw-notmatch[regex]::Escape($token)){throw "Firewall control missing $token"}}
$release=Get-Content -Raw (Join-Path $root 'scripts\new-release-security-evidence.ps1');foreach($token in @('Policy ID/purpose is not independently approved','Approved policy digest mismatch','RS256','3072','SPDX-2.3','Scan digest coverage does not exactly match','Unknown normalized vulnerability severity','source_repository_uri','required_materials','git -C $repo status --porcelain')){if($release-notmatch[regex]::Escape($token)){throw "Release evidence control missing $token"}}
$ownerRoot='C:\ProgramData\Gbuzz\trust\owner-bootstrap.json';$digestPin='config\owner-bootstrap.sha256';if($release-notmatch[regex]::Escape($ownerRoot)-or$fw-notmatch[regex]::Escape($ownerRoot)-or$release-notmatch[regex]::Escape($digestPin)-or$fw-notmatch[regex]::Escape($digestPin)){throw 'Release and exposure verification do not terminate at the same compiled owner root'};if($release-notmatch'release_security_evidence'-or$fw-notmatch'lan_exposure_activation'){throw 'Child policy purpose separation is missing'}

# Fail-closed executable negatives that do not require production ACL/firewall mutation.
$scratch=Join-Path ([IO.Path]::GetTempPath()) ('gbuzz-lan-negative-'+[guid]::NewGuid().ToString('n'));New-Item -ItemType Directory $scratch|Out-Null
try{
  $digest='a'*64;$image="registry/gbuzz@sha256:$digest";@{services=@{relay=@{image=$image}}}|ConvertTo-Json -Depth 4|Set-Content "$scratch/compose.json";Set-Content "$scratch/images.txt" $image
  @{spdxVersion='SPDX-2.3';SPDXID='SPDXRef-DOCUMENT';packages=@(@{externalRefs=@(@{referenceType='gitoid';referenceLocator=('sha256:'+$digest)})})}|ConvertTo-Json -Depth 7|Set-Content "$scratch/sbom.json"
  @{version='2.1.0';runs=@(@{tool=@{driver=@{name='approved-scanner';version='1'}};invocations=@(@{executionSuccessful=$true});properties=@{targetDigest=$digest};results=@()})}|ConvertTo-Json -Depth 8|Set-Content "$scratch/scan.json"
  Set-Content "$scratch/sigs.json" '[]';Set-Content "$scratch/prov.json" '[]'
  # A caller-created config is deliberately rejected before any caller-provided key is trusted because its ACL inherits.
  @{schema_version=1;approved_root=$scratch;approved_deployment_root=$root;policies=@()}|ConvertTo-Json|Set-Content "$scratch/config.json"
  $evidenceArgs=@{ResolvedComposeJson="$scratch/compose.json";BuildImageManifest="$scratch/images.txt";SbomPath="$scratch/sbom.json";VulnerabilitySarif="$scratch/scan.json";SignatureReport="$scratch/sigs.json";ProvenanceReport="$scratch/prov.json";PolicyId='attacker-policy';OutputPath="$scratch/out.json"}
  Throws {& (Join-Path $root 'scripts\new-release-security-evidence.ps1') @evidenceArgs} 'Cannot find path'
  # Unknown severity and unrelated digest are represented as explicit fail-closed branches, not generic SARIF levels.
  if($release-notmatch "severity-notin@\('critical','high','medium','low','none'\)"){throw 'Unknown severity branch missing'}
  if($release-notmatch 'Compare-Object \$digests \(\$scanCoverage'){throw 'One-to-one scan coverage branch missing'}
}finally{Remove-Item $scratch -Recurse -Force}
Write-Output 'LAN security safe negative tests passed.'
