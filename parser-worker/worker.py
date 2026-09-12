"""One-shot, digest-bound document parser worker. Communicates only over stdio."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from document_parsing import extract

CONTRACT_VERSION = "gcor.parser.v1"
MAX_INPUT = int(os.getenv("PARSER_MAX_INPUT_BYTES", str(50 * 1024 * 1024)))
MAX_OUTPUT = int(os.getenv("PARSER_MAX_OUTPUT_BYTES", str(10 * 1024 * 1024)))
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ANCHOR = re.compile(r"^\[([^\]\r\n]+)\]\r?\n", re.MULTILINE)


def process(request: dict) -> dict:
    if request.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unsupported parser contract")
    digest = str(request.get("source_sha256", "")).casefold()
    size = request.get("source_size")
    media_type = str(request.get("declared_media_type", ""))
    if not HEX64.fullmatch(digest) or not isinstance(size, int) or size < 0 or size > MAX_INPUT:
        raise ValueError("invalid digest-bound source metadata")
    source = Path("/input/source")
    content = source.read_bytes()
    if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("staged source digest or size mismatch")
    text = extract(content, media_type)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_OUTPUT:
        raise ValueError("structured extraction exceeds output limit")
    anchors = [{"anchor": match.group(1), "offset": match.start()} for match in ANCHOR.finditer(text)]
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "complete",
        "source_sha256": digest,
        "source_size": size,
        "declared_media_type": media_type,
        "detected_media_type": media_type,
        "parser_version": "1",
        "tool_versions": {"pypdf": __import__("pypdf").__version__},
        "text": text,
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "anchors": anchors,
        "warnings": [],
    }


def main() -> int:
    try:
        result = process(json.loads(sys.stdin.buffer.read(MAX_OUTPUT + 1)))
        payload = json.dumps(result, separators=(",", ":")).encode()
        if len(payload) > MAX_OUTPUT:
            raise ValueError("worker response exceeds output limit")
        sys.stdout.buffer.write(payload)
        return 0
    except Exception as error:
        sys.stderr.write(json.dumps({"contract_version": CONTRACT_VERSION, "status": "failed", "error": str(error)[:500]}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
