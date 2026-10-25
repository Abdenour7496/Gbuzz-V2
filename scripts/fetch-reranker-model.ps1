[CmdletBinding()]
param(
    [string]$Repo = 'gpustack/bge-reranker-v2-m3-GGUF',
    [string]$Quant = 'Q8_0',
    [string]$Destination = ''
)
# Downloads a GGUF reranker once, so the reranker service can run with no network access.
# The file is verified against the SHA-256 that Hugging Face records for it (Git LFS oid) and the
# result is written to SHA256SUMS next to the model. Run on a host with internet access.
$ErrorActionPreference = 'Stop'; Set-StrictMode -Version Latest
if (-not $Destination) { $Destination = Join-Path (Split-Path -Parent $PSScriptRoot) 'models\reranker' }
New-Item -ItemType Directory -Force $Destination | Out-Null
$tree = Invoke-RestMethod -Uri "https://huggingface.co/api/models/$Repo/tree/main"
$file = @($tree | Where-Object { $_.type -eq 'file' -and $_.path -like '*.gguf' -and $_.path -match [regex]::Escape($Quant) })
if ($file.Count -ne 1) { throw "Expected exactly one $Quant GGUF in $Repo, found $($file.Count)." }
$file = $file[0]
if (-not ($file.PSObject.Properties.Name -contains 'lfs') -or -not $file.lfs.oid) { throw 'Hugging Face did not report a SHA-256 for the file; refusing to continue.' }
$expected = $file.lfs.oid.ToLowerInvariant()
$target = Join-Path $Destination ([IO.Path]::GetFileName($file.path))
if (Test-Path $target) {
    $have = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
    if ($have -eq $expected) { Write-Host "Already present and verified: $target"; exit 0 }
}
$partial = "$target.partial"
Invoke-WebRequest -Uri "https://huggingface.co/$Repo/resolve/main/$($file.path)" -OutFile $partial -UseBasicParsing
$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $partial).Hash.ToLowerInvariant()
if ($actual -ne $expected) { Remove-Item -LiteralPath $partial -Force; throw "SHA-256 mismatch: expected $expected, got $actual" }
Move-Item -LiteralPath $partial -Destination $target -Force
"$actual  $([IO.Path]::GetFileName($target))" | Set-Content -Encoding ascii (Join-Path $Destination 'SHA256SUMS')
Write-Host "Verified $target ($actual). Set RERANKER_MODEL_FILE=$([IO.Path]::GetFileName($target)) if it differs from the default."
