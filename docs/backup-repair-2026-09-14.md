# Backup activation and recovery verification — 14 September 2026

Initialized protected local storage at `C:\ProgramData\Gbuzz\encrypted-backups`
and a randomly generated 64-byte encryption key, protected with CurrentUser DPAPI
at `C:\ProgramData\Gbuzz\secrets\backup-key.dpapi`. No key material is recorded
in this repository. An independent off-host recovery-key copy and off-host backup
destination have **not** been configured; this activation provides local recovery
only and does not establish clean-host disaster recovery.

Updated the existing node-exporter service to mount the backup metrics directory.
Registered `Gbuzz-Encrypted-Backup` under the current interactive Windows identity
with a six-hour interval. Docker Desktop and that user session must be available.

Fixed issues discovered by executing the existing scripts:

- Resolve script-relative parameter defaults after Windows PowerShell initializes
  the script context.
- Load the DPAPI assembly explicitly in Windows PowerShell.
- Handle empty retention directories under strict mode.
- Feed reference SQL directly to psql and stop on SQL errors.
- Extract verification evidence separately from its source TAR.
- Restore global database roles before restoring policies in the isolated drill.
- Write Prometheus textfiles with LF line endings.

Backup `20260914T102802Z-e4a88bf9` completed and passed encryption authentication,
manifest and file hash verification: 1,220 files, 1,426 object versions and delete
markers across five buckets, and 885 resolved database references.

The isolated sample restore passed at 10:30 UTC: 1,728 relay events, 230 knowledge
entries, samples from all five buckets, 217 preserved delete markers, and a valid
relay Git archive. Production data was not restored or modified by the drill.

Validation also includes the Windows PowerShell reliability suite (with empty
retention and LF textfile regressions), eight reliability automation tests, and
five backup evidence tests. The scheduled task was manually triggered and returned
exit code zero.
