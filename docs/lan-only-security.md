# LAN-only security

The base stack now publishes the relay on loopback by default. Android and other
LAN clients use `docker-compose.lan.yml`, which requires an explicit RFC1918 host
address and replaces, rather than appends to, the base port publication.

Choose a stable private host address and an owner-approved `/24` or narrower
client CIDR; do not guess them. Prefer a DHCP reservation. Before rollout:

```powershell
$env:BUZZ_LAN_BIND_ADDR='192.168.1.10'
$env:BUZZ_LAN_REMOTE_CIDR='192.168.1.0/24'
./scripts/verify-lan-compose.ps1
./scripts/configure-gbuzz-lan-boundary.ps1 -Mode Audit -BindAddress $env:BUZZ_LAN_BIND_ADDR -RemoteCidr $env:BUZZ_LAN_REMOTE_CIDR
```

During the window, apply with `-OwnerApprovedCidr` before deploying the LAN overlay. The
rule allows only the chosen local address, TCP port, remote CIDR, and Windows
Private profile. Confirm an approved Android/desktop client can connect and a
client outside the CIDR cannot. Run `test-gbuzz-lan-reachability.ps1` from one
approved client with `-Expected Allowed` and one excluded routed client with
`-Expected Denied`; retain both JSON results. Audit also rejects overlapping
enabled inbound allow rules on the relay address/port. Disable router port
forwarding and UPnP exposure.
Rollback removes only the named managed firewall rule and redeploys the prior
loopback-only plan; it never opens a wildcard listener.

Audit secret ACLs with `protect-gbuzz-secrets.ps1 -Mode Audit`. Apply replaces
inheritance with Full Control for only the dedicated operating identity,
Administrators, and SYSTEM, then verifies fail-closed. It records prior SDDL for
rollback without reading or logging secrets. Rollback can restore the insecure
old ACL, so use it only under controlled emergency approval.

Public access later requires a named domain, authenticated TLS edge, certificate
renewal monitoring, WebSocket forwarding, rate limits, origin/trusted-proxy
policy, penetration testing, and separate firewall/NAT approval. PostgreSQL,
Redis, MinIO, Ollama, monitoring, and admin endpoints remain private.

Production must use a digest lock. Render the exact Compose JSON and run
`new-release-security-evidence.ps1`. It includes runtime and build-only images,
requires every reference to use `@sha256:<64 hex>`, parses the vulnerability SARIF
and blocks on findings, and requires exactly one verified signature and provenance
record per image. Provenance must identify its builder and the exact Git commit.
The resulting manifest hashes all inputs, SBOM, image list, and commit.
