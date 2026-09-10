[CmdletBinding()]
param(
    [string]$SourceRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) 'backups/buzz-desktop-source'),
    [string]$ToolsRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) 'backups/build-tools'),
    [ValidateRange(1,8)][int]$BuildJobs = 2
)
$ErrorActionPreference='Stop'
$env:CARGO_HOME=Join-Path $ToolsRoot 'cargo'
$env:RUSTUP_HOME=Join-Path $ToolsRoot 'rustup'
$env:PATH="$(Join-Path $env:CARGO_HOME 'bin');$env:PATH"
$env:CARGO_BUILD_JOBS="$BuildJobs"
$env:CMAKE_POLICY_VERSION_MINIMUM='3.5'
$vsPath=Join-Path $ToolsRoot 'vs'
Import-Module (Join-Path $vsPath 'Common7/Tools/Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $vsPath -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64' | Out-Null
$env:PATH="$(Join-Path $vsPath 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin');$env:PATH"
# MSBuild's tracking files exceed MAX_PATH under Cargo's nested build directory.
# Build the same locked Opus sources at a shorter path and use its supported override.
$opusRoot=Join-Path $ToolsRoot 'opus'
if(-not (Test-Path (Join-Path $opusRoot 'lib/opus.lib'))){
    $opusSources=@(Get-ChildItem (Join-Path $env:CARGO_HOME 'registry/src/*/audiopus_sys-0.2.2/opus') -Directory | Where-Object Name -eq 'opus')
    if($opusSources.Count -ne 1){throw 'Fetch the locked Cargo dependencies before building Opus.'}
    cmake -S $opusSources[0].FullName -B (Join-Path $ToolsRoot 'opus-build') -G 'Visual Studio 17 2022' -A x64 "-DCMAKE_INSTALL_PREFIX=$opusRoot" -DBUILD_SHARED_LIBS=OFF
    if($LASTEXITCODE -ne 0){throw 'Opus configuration failed.'}
    cmake --build (Join-Path $ToolsRoot 'opus-build') --config Release --target INSTALL --parallel $BuildJobs
    if($LASTEXITCODE -ne 0){throw 'Opus build failed.'}
}
$env:OPUS_LIB_DIR=$opusRoot
Push-Location (Join-Path $SourceRoot 'desktop')
try {
    pnpm exec tauri build --target x86_64-pc-windows-msvc --bundles nsis --config src-tauri/tauri.windows.conf.json -- --locked
    if($LASTEXITCODE -ne 0){throw 'Native Buzz Desktop build failed; the installed application was not changed.'}
    Get-ChildItem -LiteralPath (Join-Path $SourceRoot 'desktop/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis') -Filter '*.exe' |
        Select-Object FullName,Length
} finally {Pop-Location}
