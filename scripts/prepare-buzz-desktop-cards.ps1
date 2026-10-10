[CmdletBinding()]
param(
    [string]$SourceRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) 'backups/buzz-desktop-source'),
    [Parameter(Mandatory)][ValidatePattern('^[0-9a-f]{64}$')][string]$KnowledgePubkey,
    [switch]$Build
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
$overlay=Join-Path $repoRoot 'clients/buzz-desktop'
$actual=git -C $SourceRoot rev-parse HEAD
if($LASTEXITCODE -ne 0 -or $actual -ne '2b4b138dc5cf2d9cc1a0ceb21d9063ff56fe8bf4') {
    throw 'Use a separate checkout of Buzz Desktop desktop-v0.5.26 at the pinned commit.'
}
$patch=Join-Path $overlay 'MessageRow.patch'
# Windows PowerShell 5.1 turns redirected native stderr into a terminating error under Stop;
# the reverse check is expected to fail when the patch is not yet applied.
$ErrorActionPreference='Continue'
git -C $SourceRoot apply --reverse --check $patch 2>$null | Out-Null
$alreadyApplied = ($LASTEXITCODE -eq 0)
$ErrorActionPreference='Stop'
if(-not $alreadyApplied) {
    git -C $SourceRoot apply --check $patch
    if($LASTEXITCODE -ne 0){throw 'Message renderer differs; review the patch before applying.'}
    git -C $SourceRoot apply $patch
    if($LASTEXITCODE -ne 0){throw 'Unable to apply native card integration.'}
}
$target=Join-Path $SourceRoot 'desktop/src/features/messages/ui'
foreach($name in @('KnowledgeCard.tsx','KnowledgeCardView.tsx','knowledgeCardModel.ts','knowledgeCard.test.mjs','KnowledgeCardView.test.mjs')) {
    Copy-Item -LiteralPath (Join-Path $overlay $name) -Destination (Join-Path $target $name)
}
if($Build) {
    Push-Location (Join-Path $SourceRoot 'desktop')
    $priorKey=$env:VITE_BUZZ_KNOWLEDGE_PUBKEY
    try {
        $env:VITE_BUZZ_KNOWLEDGE_PUBKEY=$KnowledgePubkey
        pnpm install --frozen-lockfile
        if($LASTEXITCODE -ne 0){throw 'Dependency installation failed'}
        node --import ./test-loader.mjs --experimental-strip-types --test src/features/messages/ui/knowledgeCard.test.mjs src/features/messages/ui/KnowledgeCardView.test.mjs
        if($LASTEXITCODE -ne 0){throw 'Card tests failed'}
        pnpm build
        if($LASTEXITCODE -ne 0){throw 'Desktop frontend build failed'}
    } finally {$env:VITE_BUZZ_KNOWLEDGE_PUBKEY=$priorKey;Pop-Location}
}
Write-Output 'Native card sources prepared. A Windows native build and installation are still required for the installed app.'
