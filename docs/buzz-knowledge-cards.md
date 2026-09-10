# Native Buzz knowledge cards

Living-knowledge extensions add revision proposals, change review, related pages, history, answer preservation and maintenance controls. See [the living knowledge workflow](buzz-living-knowledge.md) for commands, permissions and limits.

The Knowledge agent attaches a versioned `gcor-card` tag to its ordinary signed replies. The original message text remains intact, so unmodified Buzz clients continue to show commands and evidence. No new relay event kind, identity mechanism or public endpoint is introduced.

The Desktop overlay targets the installed Buzz Desktop 0.5.23, commit `b9392d9d78744df365f9276e1ffe8c1baa5ea903`. It renders proposal, document, answer and workspace cards in the existing message timeline. Buttons inspect current knowledge, approve/reject a proposal revision, draft from discussion, open the library, ask a question, or navigate to an original supporting message.

## Security and interaction

- The renderer requires the exact raw event signer configured with `VITE_BUZZ_KNOWLEDGE_PUBKEY`. Display names, delegated authors and other agent identities do not grant card trust. Missing configuration falls back to ordinary text.
- The payload and message channel tag must both match the current channel. Malformed, duplicated, oversized, edited or unsupported cards fall back to text.
- Cards contain inert text and identifiers, never executable actions or arbitrary navigation URLs. The client constructs commands from a fixed allowlist. React renders text without raw HTML.
- Approval/rejection buttons appear only for a current human owner/admin. The backend independently checks signatures, current membership, agent sponsorship and the exact reviewed revision.
- Sending a request disables repeated clicks while pending. A sent review hides its review buttons locally; the UI says the request was sent, not that approval succeeded. The agent's reply confirms the result. Older cards remain snapshots; Inspect latest refreshes through a new signed chat command.
- Questions and action results remain channel-visible. Existing restricted-document exclusions still apply.

## Build and install

The maintained source overlay is in `clients/buzz-desktop`; upstream source stays in an isolated checkout rather than replacing this repository with a fork. Apply and build the frontend with:

```powershell
pwsh -File scripts/prepare-buzz-desktop-cards.ps1 -SourceRoot C:/Gbuzz/backups/buzz-desktop-source -KnowledgePubkey <dedicated-agent-public-key> -Build
```

The public key is not a private key. Preserve the same configuration when producing the native bundle. The current local checkout has its configured public key in `desktop/.env.local`.

The build above produces the Buzz frontend, not a Windows installer. Native packaging requires Rust's MSVC toolchain, Microsoft C++ Build Tools/Windows SDK, and the sidecar binaries required by Buzz's Tauri bundle configuration. These prerequisites were not installed on this machine during the initial checks. Do not replace the running Buzz executable with frontend assets or claim the cards are active in the installed app until the native package is built and installed. Preserve the current installer and identity data for rollback.

## Verification

The complete Desktop frontend passed TypeScript and production builds. Five model/component tests cover signer/channel rejection, malformed payloads, revision-bound commands, role visibility, pending-state controls, HTML escaping, source actions and question submission. The actual component preview was visually inspected using sample data. Backend card tests verify text fallback and bounded inert metadata. The preview is a UI test fixture, not another knowledge workspace.

Deployment record: card metadata is deployed on the Knowledge agent, with the prior image retained as gbuzz-buzz-knowledge:before-cards. Sixty-six backend tests and both isolated chat lifecycle suites passed; the real Buzz relay accepted card-bearing replies. Rust 1.95.0 and Microsoft C++ Build Tools/Windows SDK were installed with user approval. The native Windows release build and NSIS packaging completed successfully. With user approval, Buzz and its running agent processes were closed, the installer completed with exit 0, and Buzz was reopened. The installed executable hash matches the built artifact; all five installed sidecars match the original verified copies. The installed startup probe passed with exit 0 and owner-only enforcement false. Previous application binaries are retained in `backups/releases/buzz-before-cards` for rollback. Full interaction in the installed native UI remains to be verified.

Installer: `backups/releases/Buzz-0.5.23-knowledge-cards-setup.exe` (55,325,044 bytes). SHA-256: `48D580B8B0B0943F27677C2F72EFE8BAE618428898A889ED505A5D885731E352`. This is a locally built, unsigned custom package, not an official signed Buzz release. Its executable passed the read-only startup probe (exit 0, owner-only enforcement false). This probe verifies executable loading and the policy setting; it does not replace a full installed-app interaction check.

The Windows package is built with scripts/build-buzz-desktop-windows.ps1. Its five agent sidecars are copied from the current installation and verified by SHA-256; the installed Desktop policy probe reports owner-only enforcement false, matching the custom build default. Build provenance is recorded under backups/buzz-cards-build-manifest.json.

On this machine, MSBuild's Opus tracking-file paths exceeded the Windows path limit inside Cargo's nested output folder. The build script compiles the same locked `audiopus_sys 0.2.2` vendored Opus sources in `backups/build-tools/opus-build`, installs the static library under `backups/build-tools/opus`, and supplies the dependency's supported `OPUS_LIB_DIR` override. Neither the dependency source nor Cargo lockfile is modified.
