#!/bin/sh
# Downloads a GGUF reranker once, so the reranker service can run with no network access.
# Verified against the SHA-256 Hugging Face records for the file (Git LFS oid). Needs curl + python3.
set -eu
REPO=${1:-gpustack/bge-reranker-v2-m3-GGUF}
QUANT=${2:-Q8_0}
DEST=${3:-$(dirname "$0")/../models/reranker}
mkdir -p "$DEST"
meta=$(curl -fsSL "https://huggingface.co/api/models/$REPO/tree/main" | python3 -c '
import json, sys
quant = sys.argv[1]
files = [f for f in json.load(sys.stdin) if f.get("type") == "file" and f["path"].endswith(".gguf") and quant in f["path"]]
if len(files) != 1 or not (files[0].get("lfs") or {}).get("oid"):
    sys.exit(f"expected exactly one {quant} GGUF with a recorded SHA-256, found {len(files)}")
print(files[0]["path"], files[0]["lfs"]["oid"].lower())' "$QUANT")
path=${meta% *}; expected=${meta#* }
name=$(basename "$path"); target="$DEST/$name"
if [ -f "$target" ] && [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$expected" ]; then echo "Already present and verified: $target"; exit 0; fi
curl -fL --retry 3 -o "$target.partial" "https://huggingface.co/$REPO/resolve/main/$path"
actual=$(sha256sum "$target.partial" | cut -d' ' -f1)
if [ "$actual" != "$expected" ]; then rm -f "$target.partial"; echo "SHA-256 mismatch: expected $expected, got $actual" >&2; exit 1; fi
mv "$target.partial" "$target"
echo "$actual  $name" > "$DEST/SHA256SUMS"
echo "Verified $target ($actual). Set RERANKER_MODEL_FILE=$name if it differs from the default."
