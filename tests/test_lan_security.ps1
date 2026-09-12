$ErrorActionPreference='Stop';$root=Split-Path -Parent $PSScriptRoot;Import-Module (Join-Path $root 'scripts\LanSecurity.psm1') -Force
function Throws([scriptblock]$Action,[string]$Message){$failed=$false;try{&$Action}catch{if($_.Exception.Message-notmatch[regex]::Escape($Message)){throw};$failed=$true};if(-not$failed){throw "Expected failure containing: $Message"}}
foreach($address in @('10.1.2.3','172.31.2.3','192.168.20.4')){if(-not(Test-GbuzzPrivateIPv4 $address)){throw "Private rejected: $address"}};foreach($address in @('0.0.0.0','127.0.0.1','8.8.8.8')){if(Test-GbuzzPrivateIPv4 $address){throw "Unsafe accepted: $address"}}
if(-not(Test-GbuzzAddressInCidr '192.168.20.4' '192.168.20.0/24')){throw 'Member rejected'};if(Test-GbuzzAddressInCidr '192.168.21.4' '192.168.20.0/24'){throw 'Outsider accepted'};if(-not(Test-GbuzzAddressInAnyCidr '192.168.20.4' '192.168.20.0/25')){throw 'Generic CIDR overlap failed'};if(-not(Test-GbuzzAddressInRange '192.168.20.4' '192.168.20.1' '192.168.20.10')){throw 'Range overlap failed'}
$base=Get-Content -Raw (Join-Path $root docker-compose.yml);if($base-notmatch'BUZZ_HTTP_BIND_ADDR:-127\.0\.0\.1'){throw 'Base bind not loopback'};$lan=Get-Content -Raw (Join-Path $root docker-compose.lan.yml);foreach($token in @('!override','BUZZ_LAN_BIND_ADDR:?set')){if($lan-notmatch[regex]::Escape($token)){throw "LAN overlay missing $token"}}
$acl=Get-Content -Raw (Join-Path $root 'scripts\protect-gbuzz-secrets.ps1');foreach($token in @('Post-write ACL verification failed','ACL rollback verification failed','target_set_digest','Manifest MAC verification failed','Reparse-point secret path rejected')){if($acl-notmatch[regex]::Escape($token)){throw "ACL control missing $token"}}
$fw=Get-Content -Raw (Join-Path $root 'scripts\configure-gbuzz-lan-boundary.ps1');foreach($token in @("'TCP','Any',256",'Port-Overlaps','Address-Overlaps','Get-NetNatStaticMapping','portproxy','Docker/WSL','Post-write firewall verification failed','firewall rollback verification failed')){if($fw-notmatch[regex]::Escape($token)){throw "Firewall control missing $token"}}
$composeVerifier=Get-Content -Raw (Join-Path $root 'scripts\verify-lan-compose.ps1');foreach($token in @('lan-compose.synthetic.env','docker-compose.production.yml','Non-relay services publish beyond loopback','Wildcard publication found')){if($composeVerifier-notmatch[regex]::Escape($token)){throw "Compose verifier missing $token"}}

$scratch=Join-Path ([IO.Path]::GetTempPath()) ('gbuzz-evidence-'+[guid]::NewGuid().ToString('n'));New-Item -ItemType Directory $scratch|Out-Null
try{
  $digest='a'*64;$image="registry/gbuzz@sha256:$digest";$commit=git -C $root rev-parse HEAD
  @{services=@{relay=@{image=$image}}}|ConvertTo-Json -Depth 4|Set-Content "$scratch/compose.json";Set-Content "$scratch/images.txt" $image
  @{spdxVersion='SPDX-2.3';SPDXID='SPDXRef-DOCUMENT';creationInfo=@{created='2026-01-01T00:00:00Z';creators=@('Tool: test')};packages=@(@{name='gbuzz';SPDXID='SPDXRef-Package';externalRefs=@(@{referenceType='purl';referenceLocator='pkg:generic/gbuzz@1'})})}|ConvertTo-Json -Depth 8|Set-Content "$scratch/sbom.json"
  @{version='2.1.0';runs=@(@{tool=@{driver=@{name='scanner';version='1'}};invocations=@(@{executionSuccessful=$true});results=@()})}|ConvertTo-Json -Depth 8|Set-Content "$scratch/vuln.sarif"
  $rsa=[Security.Cryptography.RSA]::Create(2048);$pub=$rsa.ExportParameters($false);@{schema_version=1;owner_approved=$true;policy_id='test-policy';signature_identity_regex='^release@example$';signature_issuer_regex='^test-issuer$';allowed_builder_ids=@('test-builder');trusted_keys=@(@{key_id='test-key';modulus_b64=[Convert]::ToBase64String($pub.Modulus);exponent_b64=[Convert]::ToBase64String($pub.Exponent)})}|ConvertTo-Json -Depth 8|Set-Content "$scratch/policy.json"
  function Signed($payload){$bytes=[Text.Encoding]::UTF8.GetBytes(($payload|ConvertTo-Json -Depth 8 -Compress));@{key_id='test-key';payload_b64=[Convert]::ToBase64String($bytes);signature_b64=[Convert]::ToBase64String($rsa.SignData($bytes,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1))}}
  @((Signed ([ordered]@{kind='image-signature';image=$image;identity='release@example';issuer='test-issuer'})))|ConvertTo-Json -Depth 6|Set-Content "$scratch/signatures.json"
  @((Signed ([ordered]@{kind='slsa-provenance';image=$image;subject_sha256=$digest;builder_id='test-builder';source_commit=$commit;materials=@(@{uri='git+https://example.invalid/repo';digest=@{sha256=$commit+$commit.Substring(0,24)}})})))|ConvertTo-Json -Depth 8|Set-Content "$scratch/provenance.json"
  $evidenceArgs=@{ResolvedComposeJson="$scratch/compose.json";BuildImageManifest="$scratch/images.txt";SbomPath="$scratch/sbom.json";VulnerabilitySarif="$scratch/vuln.sarif";SignatureReport="$scratch/signatures.json";ProvenanceReport="$scratch/provenance.json";TrustPolicy="$scratch/policy.json";OutputPath="$scratch/evidence.json"}
  & (Join-Path $root 'scripts\new-release-security-evidence.ps1') @evidenceArgs|Out-Null;if(-not(Test-Path "$scratch/evidence.json")){throw 'Evidence output missing'}
  Set-Content "$scratch/signatures.json" '[{"key_id":"test-key","payload_b64":"e30=","signature_b64":"AA=="}]';Throws {& (Join-Path $root 'scripts\new-release-security-evidence.ps1') @evidenceArgs} 'invalid or untrusted'
  Set-Content "$scratch/vuln.sarif" '{"version":"2.1.0","runs":[]}';Throws {& (Join-Path $root 'scripts\new-release-security-evidence.ps1') @evidenceArgs} 'no runs'
}finally{$rsa.Dispose();Remove-Item $scratch -Recurse -Force}

# Effective disposable ACL transaction: apply, audit, authenticated rollback, and tamper rejection.
$aclScratch=Join-Path ([IO.Path]::GetTempPath()) ('gbuzz-acl-'+[guid]::NewGuid().ToString('n'));New-Item -ItemType Directory $aclScratch|Out-Null
try{
  $secret=Join-Path $aclScratch 'secret.txt';[IO.File]::WriteAllText($secret,'synthetic');$before=(Get-Acl -LiteralPath $secret).GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)
  $keyPath=Join-Path $aclScratch 'manifest.key';$key=New-Object byte[] 32;[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($key);[IO.File]::WriteAllBytes($keyPath,$key)
  $audit=Join-Path $aclScratch 'audit';$identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name
  $result=& (Join-Path $root 'scripts\protect-gbuzz-secrets.ps1') -Mode Apply -OperatingIdentity $identity -SecretPaths $secret -EvidenceRoot $audit -ManifestMacKeyPath $keyPath|ConvertFrom-Json
  $auditResult=@(& (Join-Path $root 'scripts\protect-gbuzz-secrets.ps1') -Mode Audit -OperatingIdentity $identity -SecretPaths $secret);if(($auditResult|ConvertFrom-Json).compliant-ne$true){throw 'Disposable ACL apply did not verify'}
  & (Join-Path $root 'scripts\protect-gbuzz-secrets.ps1') -Mode Rollback -OperatingIdentity $identity -SecretPaths $secret -RollbackManifest $result.rollback_manifest -ManifestMacKeyPath $keyPath
  if((Get-Acl -LiteralPath $secret).GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)-ne$before){throw 'Disposable ACL rollback did not restore exact access SDDL'}
  $tampered=Get-Content -LiteralPath $result.rollback_manifest -Raw|ConvertFrom-Json;$tampered.host_id='other-host';$tampered|ConvertTo-Json -Depth 9|Set-Content -LiteralPath $result.rollback_manifest
  Throws {& (Join-Path $root 'scripts\protect-gbuzz-secrets.ps1') -Mode Rollback -OperatingIdentity $identity -SecretPaths $secret -RollbackManifest $result.rollback_manifest -ManifestMacKeyPath $keyPath} 'another host'
}finally{Remove-Item $aclScratch -Recurse -Force}
Write-Output 'LAN security negative tests passed.'
