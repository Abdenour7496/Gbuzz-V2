"""One-shot, digest-bound document parser worker. Communicates only over stdio."""
import hashlib
import json
import os
import re
import socket
import struct
import sys
from pathlib import Path

from document_parsing import extract

CONTRACT_VERSION = "gcor.parser.v1"
MAX_INPUT = int(os.getenv("PARSER_MAX_INPUT_BYTES", str(50 * 1024 * 1024)))
MAX_OUTPUT = int(os.getenv("PARSER_MAX_OUTPUT_BYTES", str(10 * 1024 * 1024)))
CONNECTION_TIMEOUT = float(os.getenv("PARSER_CONNECTION_TIMEOUT_SECONDS", "15"))
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ANCHOR = re.compile(r"^\[([^\]\r\n]+)\]\r?\n", re.MULTILINE)


def process(request: dict, content: bytes | None = None) -> dict:
    if request.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("unsupported parser contract")
    digest = str(request.get("source_sha256", "")).casefold()
    size = request.get("source_size")
    media_type = str(request.get("declared_media_type", ""))
    if not HEX64.fullmatch(digest) or not isinstance(size, int) or size < 0 or size > MAX_INPUT:
        raise ValueError("invalid digest-bound source metadata")
    content = Path("/input/source").read_bytes() if content is None else content
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
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        return serve()
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


def _read(connection: socket.socket, length: int) -> bytes:
    value = bytearray()
    while len(value) < length:
        chunk = connection.recv(length - len(value))
        if not chunk:
            raise ConnectionError("client closed early")
        value.extend(chunk)
    return bytes(value)


def _response_payload(result: dict) -> bytes:
    payload = json.dumps(result, separators=(",", ":")).encode()
    if len(payload) <= MAX_OUTPUT:
        return payload
    return json.dumps({
        "contract_version": CONTRACT_VERSION,
        "status": "failed",
        "error": "worker response exceeds output limit",
    }, separators=(",", ":")).encode()


def _handle_connection(connection: socket.socket) -> None:
    connection.settimeout(CONNECTION_TIMEOUT)
    try:
        header_length, content_length = struct.unpack("!IQ", _read(connection, 12))
        if header_length > 16_384 or content_length > MAX_INPUT:
            raise ValueError("request exceeds worker limits")
        result = process(json.loads(_read(connection, header_length)), _read(connection, content_length))
    except Exception as error:
        result = {"contract_version": CONTRACT_VERSION, "status": "failed", "error": str(error)[:500]}
    payload = _response_payload(result)
    try:
        connection.sendall(struct.pack("!I", len(payload)) + payload)
    except (BrokenPipeError, ConnectionError, OSError, socket.timeout):
        # A timed-out or disconnected client must not terminate the worker.
        return


def serve() -> int:
    path = os.getenv("PARSER_SOCKET_PATH", "/run/parser/parser.sock")
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path);os.chmod(path,0o660);server.listen(8)
    while True:
        connection, _ = server.accept()
        with connection:
            _handle_connection(connection)


if __name__ == "__main__":
    raise SystemExit(main())
