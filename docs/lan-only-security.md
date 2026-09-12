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

Audit secret ACLs with `protect-gbuzz-secrets.ps1 -Mode Audit`. The evidence root
must already exist. Apply requires a protected configuration whose ACL permits
only SYSTEM and Administrators and which pins the dedicated MAC key's canonical
path and SHA-256 identity. The evidence-root DACL participates in the same
snapshot/apply/restore transaction as every secret path. The command replaces
inheritance with Full Control for only the dedicated operating identity,
Administrators, and SYSTEM, then verifies fail-closed. It records prior SDDL for
rollback without reading or logging secrets. The rollback manifest is bound to
the host, operation, identity, canonical target-set digest, and run by HMAC.
Every rollback must supply the expected run ID and operation. Parent or final
reparse points, hard links, aliases, duplicate targets, added paths, and
cross-host/run manifests are rejected. Apply verification is inside the transaction; any write
or verification failure restores and reverifies every captured DACL. Rollback
can restore the insecure old ACL, so use it only under controlled emergency approval.

Public access later requires a named domain, authenticated TLS edge, certificate
renewal monitoring, WebSocket forwarding, rate limits, origin/trusted-proxy
policy, penetration testing, and separate firewall/NAT approval. PostgreSQL,
Redis, MinIO, Ollama, monitoring, and admin endpoints remain private.

Production must use a digest lock. Render the exact Compose JSON and run
`new-release-security-evidence.ps1` with an approved policy ID and protected
configuration. Caller input cannot supply keys, regular expressions, or a new
trust policy. The protected configuration pins the policy path, exact digest,
ID/version, owner signer key, validity/revocation state, and approved deployment
root; the policy's signed statement supplies the release keys and exact trust
rules. It includes
runtime and build-only images, requires every reference to use `@sha256:<64 hex>`,
accepts only SPDX 2.3 JSON and the policy-named scanner/SARIF profile, and requires
one-to-one SBOM and scan coverage of every runtime/build digest. Unknown schemas,
tools, severities, incomplete scans, and unapproved suppressed findings fail.
Image-signature and SLSA-provenance payloads are
verified cryptographically against trusted public keys; identity, issuer, builder,
subject digest, canonical repository root, permitted ref, source URI, clean source
commit, and exact material set must satisfy policy. RSA keys must be RS256 and at
least 3072 bits. Boolean
`verified` assertions are not accepted. The resulting manifest hashes all inputs,
SBOM, resolved image set, policy, and commit.

`verify-lan-compose.ps1` uses `config/lan-compose.synthetic.env`, which contains
only documented non-secret interpolation values, and renders the base, production,
digest-lock, and LAN overlays from a clean worktree. It inspects every published
port: relay must have exactly the approved LAN binding and every other published
service must remain on loopback. Windows firewall audit additionally inventories
active policy stores, protocol Any, port lists/ranges, address ranges/subnets,
IPv6, NAT and portproxy. Docker/WSL forwarding and routed-client reachability remain
separate required evidence. Their absence makes the exposure gate
`compliant:false`; Windows firewall state alone is never exposure proof.
