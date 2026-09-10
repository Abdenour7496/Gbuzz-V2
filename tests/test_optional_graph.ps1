$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    function Read-Config([string[]]$Extra) {
        $raw = & docker compose --env-file .env.example -f docker-compose.yml @Extra config --format json
        if ($LASTEXITCODE -ne 0) { throw 'Compose rendering failed' }
        return ($raw | ConvertFrom-Json)
    }
    $core = Read-Config @()
    $graph = Read-Config @('-f','docker-compose.graph.yml')
    $production = Read-Config @('-f','docker-compose.observability.yml','-f','docker-compose.production.yml')
    $external = Read-Config @('-f','docker-compose.graph.yml','-f','docker-compose.graph-production.yml')
    foreach ($name in @('falkordb','graphiti-mcp','graphiti-projector','graphiti-model-init')) {
        if ($core.services.PSObject.Properties.Name -contains $name) { throw "Core unexpectedly includes $name" }
        if ($production.services.PSObject.Properties.Name -contains $name) { throw "Production unexpectedly includes $name" }
        if ($graph.services.PSObject.Properties.Name -notcontains $name) { throw "Graph extension omits $name" }
    }
    foreach ($name in @('gcor-proxy','gcor-event-projector','postgres','relay')) {
        if ($core.services.PSObject.Properties.Name -notcontains $name) { throw "Core omits $name" }
    }
    if (($core.services.'ollama-init'.command -join ' ') -match 'GRAPHITI') { throw 'Core downloads graph models' }
    if ($external.services.'graphiti-model-init'.environment.GRAPHITI_LLM_API_URL -ne 'https://api.openai.com/v1') { throw 'External profile downloads local extraction model' }
    if ($graph.services.'graphiti-mcp'.depends_on.PSObject.Properties.Name -notcontains 'graphiti-model-init') { throw 'Graph model initialization dependency missing' }
    Write-Output 'Optional graph Compose checks passed: core, local graph, production core, external graph.'
} finally { Pop-Location }
