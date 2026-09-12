[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$ResolvedComposeJson,
  [Parameter(Mandatory)][string]$BuildImageManifest,
  [Parameter(Mandatory)][string]$SbomPath,
  [Parameter(Mandatory)][string]$VulnerabilitySarif,
  [Parameter(Mandatory)][string]$SignatureReport,
  [Parameter(Mandatory)][string]$ProvenanceReport,
  [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9._-]+$')][string]$PolicyId,
  [Parameter(Mandatory)][string]$ProtectedConfiguration,
  [string]$OutputPath='release-security-evidence.json'
)
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest

function Read-Json([string]$Path) { try { Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -ErrorAction Stop } catch { throw "Malformed JSON evidence: $Path" } }
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Digest([string]$Image) { if($Image-notmatch'@sha256:([0-9a-f]{64})$'){throw "Image is not digest pinned: $Image"};$Matches[1] }
function Canonical([string]$Path) { (Get-Item -LiteralPath $Path -Force -ErrorAction Stop).FullName }
function Assert-ProtectedFile([string]$Path,[string]$ApprovedRoot) {
  $full=Canonical $Path;$root=[IO.Path]::GetFullPath($ApprovedRoot).TrimEnd('\')+'\'
  if(-not($full+'\').StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){throw "Protected file is outside approved root: $full"}
  $cursor=Get-Item -LiteralPath $full -Force
  while($cursor){if($cursor.Attributes-band[IO.FileAttributes]::ReparsePoint){throw "Protected path traverses reparse point: $($cursor.FullName)"};if($cursor.FullName.TrimEnd('\')-ieq$root.TrimEnd('\')){break};$cursor=if($cursor-is[IO.DirectoryInfo]){$cursor.Parent}else{$cursor.Directory}}
  $acl=Get-Acl -LiteralPath $full;if(-not$acl.AreAccessRulesProtected){throw "Protected file ACL inherits: $full"}
  $bad=@($acl.Access|Where-Object{$_.AccessControlType-eq'Allow'-and$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value-notin@('S-1-5-18','S-1-5-32-544')});if($bad){throw "Protected file has non-admin write authority: $full"};$full
}
function Verify-Rsa($Record,$Key,[string]$Kind) {
  if($Key.algorithm-ne'RS256'-or[int]$Key.bits-lt3072){throw "$Kind key algorithm/strength is not approved."}
  try{$data=[Convert]::FromBase64String([string]$Record.payload_b64);$sig=[Convert]::FromBase64String([string]$Record.signature_b64);$rsa=[Security.Cryptography.RSA]::Create();$p=[Security.Cryptography.RSAParameters]::new();$p.Modulus=[Convert]::FromBase64String([string]$Key.modulus_b64);$p.Exponent=[Convert]::FromBase64String([string]$Key.exponent_b64);$rsa.ImportParameters($p);if($rsa.KeySize-lt3072-or-not$rsa.VerifyData($data,$sig,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)){throw 'invalid'};$data=[Text.Encoding]::UTF8.GetString($data)|ConvertFrom-Json -ErrorAction Stop} catch {throw "$Kind cryptographic verification failed."};$data
}

$config=Read-Json $ProtectedConfiguration
if($config.schema_version-ne1-or-not$config.approved_root){throw 'Protected configuration schema is unsupported.'}
$configPath=Assert-ProtectedFile $ProtectedConfiguration ([string]$config.approved_root)
$entry=@($config.policies|Where-Object{$_.policy_id-ceq$PolicyId});if($entry.Count-ne1){throw 'Policy ID is not independently approved.'}
$policyPath=Assert-ProtectedFile ([string]$entry.path) ([string]$config.approved_root)
if((Hash $policyPath)-cne[string]$entry.sha256){throw 'Approved policy digest mismatch.'}
$policy=Read-Json $policyPath
if($policy.policy_id-cne$entry.policy_id-or$policy.version-cne$entry.version-or$policy.signer_key_id-cne$entry.signer_key_id){throw 'Approved policy identity/version/signer mismatch.'}
$now=[DateTimeOffset]::UtcNow;if($now-lt[DateTimeOffset]$policy.valid_from-or$now-gt[DateTimeOffset]$policy.valid_until-or$policy.revoked){throw 'Approved policy is expired, premature, or revoked.'}
$rootKey=@($config.owner_keys|Where-Object{$_.key_id-ceq$entry.signer_key_id-and-not$_.revoked});if($rootKey.Count-ne1){throw 'Pinned owner key is missing or revoked.'}
$signedPolicy=Verify-Rsa $policy.signature $rootKey[0] 'Policy';if((ConvertTo-Json $signedPolicy -Depth 20 -Compress)-cne(ConvertTo-Json $policy.statement -Depth 20 -Compress)){throw 'Policy signed statement mismatch.'};$rules=$signedPolicy

$repo=Canonical ([string]$rules.repository_root);if($repo-cne[IO.Path]::GetFullPath([string]$rules.repository_root)){throw 'Repository root is not canonical.'};if(-not($repo+'\').StartsWith(([IO.Path]::GetFullPath([string]$config.approved_deployment_root).TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase)){throw 'Repository root is outside approved deployment path.'}
$commit=(git -C $repo rev-parse HEAD).Trim();if($LASTEXITCODE-ne0-or$commit-notmatch'^[0-9a-f]{40}$'){throw 'Cannot resolve source commit from approved repository.'};if(git -C $repo status --porcelain){throw 'Approved repository is not clean.'};$remote=(git -C $repo remote get-url origin).Trim();if($remote-cne[string]$rules.source_repository_uri){throw 'Source repository URI mismatch.'};$branch=(git -C $repo symbolic-ref --short HEAD).Trim();if($branch-notlike[string]$rules.permitted_ref){throw 'Source ref is not permitted.'}

foreach($p in @($ResolvedComposeJson,$BuildImageManifest,$SbomPath,$VulnerabilitySarif,$SignatureReport,$ProvenanceReport)){if(-not(Test-Path -LiteralPath $p -PathType Leaf)){throw "Evidence missing: $p"}}
$compose=Read-Json $ResolvedComposeJson;$runtime=@($compose.services.PSObject.Properties|%{[string]$_.Value.image}|?{$_});$built=@(Get-Content -LiteralPath $BuildImageManifest|%{$_.Trim()}|?{$_});$images=@($runtime+$built|Sort-Object -Unique);if(-not$images.Count){throw 'Resolved image set is empty.'};$digests=@($images|%{Digest $_})
$sbom=Read-Json $SbomPath;if($sbom.spdxVersion-cne'SPDX-2.3'-or$sbom.SPDXID-cne'SPDXRef-DOCUMENT'){throw 'Only SPDX 2.3 JSON is supported.'};$sbomCoverage=@($sbom.packages|%{@($_.externalRefs)}|?{$_.referenceType-ceq'gitoid' -and $_.referenceLocator-match'^sha256:[0-9a-f]{64}$'}|%{$_.referenceLocator.Substring(7)});if($sbomCoverage.Count-ne$digests.Count-or(Compare-Object $digests ($sbomCoverage|Sort-Object -Unique))){throw 'SBOM digest coverage does not exactly match resolved images.'}
$sarif=Read-Json $VulnerabilitySarif;if($sarif.version-cne'2.1.0'){throw 'Only SARIF 2.1.0 is supported.'};$scanCoverage=@();foreach($run in @($sarif.runs)){if($run.tool.driver.name-cne[string]$rules.scanner.name-or$run.tool.driver.version-cne[string]$rules.scanner.version-or@($run.invocations).Count-ne1-or$run.invocations[0].executionSuccessful-ne$true){throw 'Unsupported or incomplete scanner run.'};$scanCoverage+=[string]$run.properties.targetDigest;foreach($result in @($run.results)){$severity=[string]$result.properties.normalizedSeverity;if($severity-notin@('critical','high','medium','low','none')){throw 'Unknown normalized vulnerability severity.'};if($severity-in@('critical','high')){if(-not$result.suppressions-or[string]$result.properties.exceptionId-notin@($rules.approved_exception_ids)){throw 'Unapproved HIGH/CRITICAL vulnerability.'}}}};if($scanCoverage.Count-ne$digests.Count-or(Compare-Object $digests ($scanCoverage|Sort-Object -Unique))){throw 'Scan digest coverage does not exactly match resolved images.'}

$keys=@($rules.release_keys|Where-Object{-not$_.revoked});$sigs=@((Read-Json $SignatureReport));$provs=@((Read-Json $ProvenanceReport));foreach($image in $images){$keyed=@($sigs|Where-Object{$_.key_id-in$keys.key_id});$valid=@($keyed|%{$k=@($keys|? key_id -CEQ $_.key_id)[0];$x=Verify-Rsa $_ $k 'Signature';if($x.image-ceq$image-and$x.identity-ceq$rules.signature_identity-and$x.issuer-ceq$rules.signature_issuer){$x}});if($valid.Count-ne1){throw "Exactly one trusted signature required: $image"};$pv=@($provs|Where-Object{$_.key_id-in$keys.key_id}|%{$k=@($keys|? key_id -CEQ $_.key_id)[0];$x=Verify-Rsa $_ $k 'Provenance';if($x.image-ceq$image){$x}});if($pv.Count-ne1){throw "Exactly one trusted provenance required: $image"};$x=$pv[0];if($x.subject_sha256-cne(Digest $image)-or$x.source_commit-cne$commit-or$x.source_repository_uri-cne$rules.source_repository_uri-or$x.builder_id-notin@($rules.allowed_builder_ids)){throw "Provenance identity mismatch: $image"};$actual=@($x.materials|%{"$($_.uri)=$($_.digest.sha256)"}|Sort-Object);$expected=@($rules.required_materials|%{"$($_.uri)=$($_.sha256)"}|Sort-Object);if(Compare-Object $actual $expected){throw "Provenance material set mismatch: $image"}}
$bound=@($ResolvedComposeJson,$BuildImageManifest,$SbomPath,$VulnerabilitySarif,$SignatureReport,$ProvenanceReport,$configPath,$policyPath|%{[ordered]@{path=[IO.Path]::GetFullPath($_);sha256=Hash $_}});[ordered]@{schema_version=4;created_at=$now.ToString('o');git_commit=$commit;policy_id=$PolicyId;policy_version=$entry.version;images=$images;inputs=$bound}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output "Owner-authenticated evidence verified for $($images.Count) images."
