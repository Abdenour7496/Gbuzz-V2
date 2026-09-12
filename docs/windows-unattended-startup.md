# Windows unattended startup runbook

The current interim design is user-logon recovery, not pre-login high availability. Docker Desktop must first start in the dedicated operating user's session. The backup and startup tasks use that exact user's `Interactive` logon token, so CurrentUser DPAPI unlocks under the same identity. Neither task runs before that user logs on. Containers then use `restart: unless-stopped`.

`config/windows-startup.compose-files.json` pins the currently reviewed overlay set: base, safeguards override, enterprise, observability, and Buzz. The startup script rejects missing files, waits at most five minutes for Docker, runs only that set, waits at most five minutes for relay/GCOR/recovery-controller/Prometheus readiness, and records JSON evidence under `C:\ProgramData\Gbuzz\startup-audit`.

Review without installing:

```powershell
powershell -NoProfile -File scripts/install-windows-startup.ps1 -WhatIf
```

After owner approval, enable Docker Desktop start-at-sign-in and install the limited, delayed logon task. The task contains only a fixed script path; it contains no credentials. Do not enable automatic login.

Before relying on it, run a controlled reboot during an approved window. Pass only if Docker starts, all required services become healthy within 15 minutes, ingestion/governance queues remain sound, and the audit file records success. Two bounded task retries are allowed. A total-host dead-man alert must be external because local Prometheus cannot report its own host loss.

If recovery is required before a person logs in, migrate to a supported always-on VM/server container runtime. Do not weaken Docker socket controls, run Desktop as an improvised privileged service, expose new ports, or store secrets in task arguments.
