# Isolated parser worker

The worker is a one-shot stdio process. The caller stages exactly one read-only
`/input/source` and supplies `gcor.parser.v1` JSON containing its SHA-256, byte
size, and declared media type. Output is accepted only when status, contract,
source digest, output digest, and anchors validate.

Required runtime controls: `--network none --read-only --user 10001:10001
--cap-drop ALL --security-opt no-new-privileges --memory 512m --cpus 1
--pids-limit 64 --tmpfs /tmp:rw,noexec,nosuid,size=64m` plus a read-only input
mount and a caller-enforced 120-second deadline. No secret or Docker socket may
be mounted. Image/OCR remains fail-closed until a separately scanned OCR engine
is added to this boundary.
