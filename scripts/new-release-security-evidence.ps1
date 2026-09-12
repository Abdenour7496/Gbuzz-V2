[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$ResolvedComposeJson,
  [Parameter(Mandatory)][string]$BuildImageManifest,
  [Parameter(Mandatory)][string]$SbomPath,
  [Parameter(Mandatory)][string]$VulnerabilitySarif,
  [Parameter(Mandatory)][string]$SignatureReport,
  [Parameter(Mandatory)][string]$ProvenanceReport,
  [Parameter(Mandatory)][string]$TrustPolicy,
  [string]$OutputPath='release-security-evidence.json'
)
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest

function Read-Json([string]$Path) {
  try { Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -ErrorAction Stop }
  catch { throw "Malformed JSON evidence: $Path" }
}
function Get-Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Get-ImageDigest([string]$Image) {
  if ($Image -notmatch '@sha256:([0-9a-f]{64})$') { throw "Image is not digest pinned: $Image" }
  $Matches[1]
}
function Test-SignedPayload($Record,$TrustedKey,[string]$Kind) {
  if (-not $Record.payload_b64 -or -not $Record.signature_b64) { throw "$Kind record is not cryptographically signed." }
  try {
    $payload=[Convert]::FromBase64String([string]$Record.payload_b64)
    $signature=[Convert]::FromBase64String([string]$Record.signature_b64)
    $rsa=[Security.Cryptography.RSA]::Create();$parameters=[Security.Cryptography.RSAParameters]::new();$parameters.Modulus=[Convert]::FromBase64String([string]$TrustedKey.modulus_b64);$parameters.Exponent=[Convert]::FromBase64String([string]$TrustedKey.exponent_b64);$rsa.ImportParameters($parameters)
    $valid=$rsa.VerifyData($payload,$signature,[Security.Cryptography.HashAlgorithmName]::SHA256,[Security.Cryptography.RSASignaturePadding]::Pkcs1)
  } catch { throw "$Kind cryptographic verification failed." }
  if (-not $valid) { throw "$Kind signature is invalid or untrusted." }
  try { $decoded=[Text.Encoding]::UTF8.GetString($payload) | ConvertFrom-Json -ErrorAction Stop }
  catch { throw "$Kind signed payload is malformed." }
  $decoded
}

$inputs=@($ResolvedComposeJson,$BuildImageManifest,$SbomPath,$VulnerabilitySarif,$SignatureReport,$ProvenanceReport,$TrustPolicy)
foreach($path in $inputs){ if(-not(Test-Path -LiteralPath $path -PathType Leaf)){ throw "Evidence missing: $path" } }
$policy=Read-Json $TrustPolicy
if($policy.schema_version-ne1 -or -not $policy.owner_approved -or -not $policy.policy_id){throw 'Trust policy is incomplete or not owner approved.'}
$keys=@($policy.trusted_keys); if(-not $keys.Count){throw 'Trust policy contains no trusted keys.'}

$compose=Read-Json $ResolvedComposeJson
$runtime=@($compose.services.PSObject.Properties | ForEach-Object {[string]$_.Value.image} | Where-Object {$_})
$built=@(Get-Content -LiteralPath $BuildImageManifest | ForEach-Object {$_.Trim()} | Where-Object {$_})
$images=@($runtime+$built | Sort-Object -Unique); if(-not $images.Count){throw 'Resolved image set is empty.'}
foreach($image in $images){[void](Get-ImageDigest $image)}
$commit=[string](git rev-parse HEAD); if($LASTEXITCODE-ne0-or$commit-notmatch'^[0-9a-f]{40}$'){throw 'Cannot resolve source commit.'}

$sbom=Read-Json $SbomPath
if($sbom.spdxVersion-notmatch'^SPDX-2\.'-or-not$sbom.SPDXID-or-not$sbom.creationInfo-or-not@($sbom.packages).Count){throw 'SBOM is malformed, empty, or not SPDX JSON.'}
$sbomRefs=@($sbom.packages | ForEach-Object {@($_.externalRefs)} | Where-Object {$_.referenceType-eq'purl' -or $_.referenceType-eq'cpe23Type'})
if(-not $sbomRefs.Count){throw 'SBOM contains no package identity references.'}

$sarif=Read-Json $VulnerabilitySarif
if($sarif.version-notmatch'^2\.1\.0$'-or-not@($sarif.runs).Count){throw 'SARIF is malformed or has no runs.'}
$findings=@(); foreach($run in @($sarif.runs)){
  if(-not$run.tool.driver.name-or-not$run.tool.driver.version){throw 'SARIF tool identity/version missing.'}
  if(-not@($run.invocations).Count-or@($run.invocations|Where-Object {$_.executionSuccessful-eq$true}).Count-ne@($run.invocations).Count){throw 'SARIF scan did not complete successfully.'}
  foreach($result in @($run.results)){
    $level=[string]$result.level; if($level-notin@('error','warning','note','none')){throw 'SARIF result has an unknown level.'}
    if($level-in@('error','warning')){$findings+=$result}
  }
}
if($findings.Count){throw "Vulnerability gate failed with $($findings.Count) HIGH/CRITICAL finding(s)."}

$signatureRecords=@((Read-Json $SignatureReport)); $provenanceRecords=@((Read-Json $ProvenanceReport))
foreach($image in $images){
  $sigMatches=@(); foreach($record in $signatureRecords){
    $key=@($keys|Where-Object {$_.key_id-eq$record.key_id}); if($key.Count-ne1){continue}
    $payload=Test-SignedPayload $record $key[0] 'Signature'
    if($payload.kind-ne'image-signature'-or$payload.image-ne$image){continue}
    if([string]$payload.identity-notmatch[string]$policy.signature_identity_regex-or[string]$payload.issuer-notmatch[string]$policy.signature_issuer_regex){throw "Signature identity/issuer policy failed: $image"}
    $sigMatches+=$payload
  }
  if($sigMatches.Count-ne1){throw "Exactly one trusted image signature is required: $image"}
  $provMatches=@(); foreach($record in $provenanceRecords){
    $key=@($keys|Where-Object {$_.key_id-eq$record.key_id}); if($key.Count-ne1){continue}
    $payload=Test-SignedPayload $record $key[0] 'Provenance'
    if($payload.kind-ne'slsa-provenance'-or$payload.image-ne$image){continue}
    if($payload.subject_sha256-ne(Get-ImageDigest $image)-or$payload.source_commit-ne$commit){throw "Provenance subject or source commit mismatch: $image"}
    if([string]$payload.builder_id-notin@($policy.allowed_builder_ids)){throw "Untrusted provenance builder: $image"}
    if(-not@($payload.materials).Count-or@($payload.materials|Where-Object {-not$_.uri-or$_.digest.sha256-notmatch'^[0-9a-f]{64}$'}).Count){throw "Provenance materials are missing or malformed: $image"}
    $provMatches+=$payload
  }
  if($provMatches.Count-ne1){throw "Exactly one trusted provenance statement is required: $image"}
}

$bound=@($inputs|ForEach-Object {[pscustomobject]@{path=[IO.Path]::GetFullPath($_);sha256=Get-Hash $_}})
$manifest=[ordered]@{schema_version=3;created_at=[DateTimeOffset]::UtcNow.ToString('o');git_commit=$commit;trust_policy_id=[string]$policy.policy_id;images=$images;runtime_images=@($runtime|Sort-Object -Unique);build_only_images=@($built|Where-Object{$_-notin$runtime}|Sort-Object -Unique);parsed_vulnerability_findings=0;inputs=$bound}
$manifest|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $OutputPath -Encoding utf8
Write-Output "Cryptographically verified and bound $($images.Count) images."
