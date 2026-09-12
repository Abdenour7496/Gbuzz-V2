Set-StrictMode -Version Latest

function Get-GbuzzRelativePath([string]$BasePath,[string]$Path) {
    $base=[Uri]::new([IO.Path]::GetFullPath($BasePath).TrimEnd('\')+'\')
    $target=[Uri]::new([IO.Path]::GetFullPath($Path))
    [Uri]::UnescapeDataString($base.MakeRelativeUri($target).ToString()).Replace('/','\')
}

function Get-GbuzzComposePlan([string]$RepoRoot,[string]$ConfigPath) {
    $root=[IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
    if(-not(Test-Path -LiteralPath $ConfigPath)){throw "Compose plan missing: $ConfigPath"}
    $config=Get-Content -Raw -LiteralPath $ConfigPath|ConvertFrom-Json
    if($config.project -ne 'gbuzz'){throw 'Compose plan project must be gbuzz.'}
    $arguments=@('--project-name',$config.project);$resolved=@()
    foreach($relative in $config.files){
        if([IO.Path]::IsPathRooted($relative)){throw "Compose plan path must be relative: $relative"}
        $full=[IO.Path]::GetFullPath((Join-Path $root $relative))
        if(-not $full.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw "Compose plan path escaped repository root: $relative"}
        if(-not(Test-Path -LiteralPath $full)){throw "Required Compose file missing: $relative"}
        if($relative -match '(?i)graphiti|docker-compose\.graph'){throw "Retired graph overlay is not permitted: $relative"}
        $arguments+=@('-f',$full);$resolved+=$full
    }
    foreach($profile in $config.profiles){if($profile -eq 'graph'){throw 'Retired graph profile is not permitted.'};$arguments+=@('--profile',$profile)}
    [pscustomobject]@{Project=$config.project;Arguments=$arguments;Files=$resolved;RequiredServices=@($config.required_services)}
}

function Assert-GbuzzRequiredServices($Plan,$ResolvedConfig) {
    $defined=@($ResolvedConfig.services.PSObject.Properties|ForEach-Object{$_.Name})
    $missing=@($Plan.RequiredServices|Where-Object{$_ -notin $defined})
    if($missing.Count){throw "Resolved Compose configuration is missing required services: $($missing -join ', ')"}
}

Export-ModuleMember -Function Get-GbuzzComposePlan,Get-GbuzzRelativePath,Assert-GbuzzRequiredServices
