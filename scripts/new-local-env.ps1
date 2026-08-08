param(
    [switch]$Force
)

$envFile = Join-Path $PSScriptRoot "..\.env"
if ((Test-Path $envFile) -and -not $Force) {
    throw ".env already exists. Use -Force only when you intend to replace its stable secrets."
}

$hexSecret = {
    param([int]$ByteCount)
    $bytes = [byte[]]::new($ByteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [Convert]::ToHexString($bytes).ToLowerInvariant()
}

$base64Secret = {
    param([int]$ByteCount)
    $bytes = [byte[]]::new($ByteCount)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [Convert]::ToBase64String($bytes).Replace('+', 'A').Replace('/', 'B').Replace('=', '')
}

$stackSecret = & $hexSecret 32
$relayPrivateKey = & $hexSecret 32
$gitHookSecret = & $hexSecret 32
$postgresPassword = & $base64Secret 32
$redisPassword = & $base64Secret 32
$s3AccessKey = "buzz$(& $hexSecret 12)"
$s3SecretKey = & $base64Secret 32

$content = @"
# Generated for a local Buzz + GCOR deployment. Keep this file private and stable.
BUZZ_IMAGE=ghcr.io/block/buzz:main
BUZZ_HTTP_PORT=3000
MINIO_API_PORT=9000
MINIO_CONSOLE_PORT=9001
BUZZ_REQUIRE_AUTH_TOKEN=false
BUZZ_REQUIRE_RELAY_MEMBERSHIP=false
BUZZ_ALLOW_IP_OA_AUTH=true
BUZZ_AUTO_MIGRATE=true
BUZZ_GIT_CONFORMANCE_PROBE=true
RUST_LOG=buzz_relay=info,buzz_db=info,buzz_auth=info,buzz_pubsub=info,tower_http=info
BUZZ_RELAY_PRIVATE_KEY=$relayPrivateKey
BUZZ_GIT_HOOK_HMAC_SECRET=$gitHookSecret
POSTGRES_DB=buzz
POSTGRES_USER=buzz
POSTGRES_PASSWORD=$postgresPassword
REDIS_PASSWORD=$redisPassword
BUZZ_S3_ACCESS_KEY=$s3AccessKey
BUZZ_S3_SECRET_KEY=$s3SecretKey
BUZZ_S3_BUCKET=buzz-media
GCOR_S3_BUCKET=buzz-gcor
EMBEDDING_BACKEND=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMS=768
OPENAI_API_KEY=
INGEST_WEBHOOK_SECRET=$stackSecret
STACK_API_SECRET=$stackSecret
ENFORCE_STACK_API_SECRET=true
GCOR_PROXY_PORT=5001
GCOR_MCP_PORT=8765
GCOR_PROXY_BIND_ADDR=127.0.0.1
GCOR_MCP_BIND_ADDR=127.0.0.1
"@

Set-Content -Path $envFile -Value $content -NoNewline -Encoding ascii
Write-Output "Created $envFile"