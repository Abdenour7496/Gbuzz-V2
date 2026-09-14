"""Digest-bound client for the networkless parser worker Unix socket."""
import hashlib
import json
import os
import socket
import struct

CONTRACT_VERSION = "gcor.parser.v1"
MAX_RESPONSE = int(os.getenv("PARSER_MAX_OUTPUT_BYTES", str(10 * 1024 * 1024)))


def extract(content: bytes, media_type: str) -> str:
    path = os.environ.get("PARSER_SOCKET_PATH")
    if not path:
        raise RuntimeError("isolated parser worker is required")
    digest = hashlib.sha256(content).hexdigest()
    request = json.dumps({"contract_version": CONTRACT_VERSION, "source_sha256": digest,
                          "source_size": len(content), "declared_media_type": media_type}, separators=(",", ":")).encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(float(os.getenv("PARSER_TIMEOUT_SECONDS", "120")))
        connection.connect(path)
        connection.sendall(struct.pack("!IQ", len(request), len(content)) + request + content)
        length = struct.unpack("!I", _read(connection, 4))[0]
        if length > MAX_RESPONSE:
            raise ValueError("parser response exceeds limit")
        response = json.loads(_read(connection, length))
    if response.get("status") != "complete" or response.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("parser returned incomplete or incompatible output")
    if response.get("source_sha256") != digest or response.get("source_size") != len(content):
        raise ValueError("parser response source binding mismatch")
    text = response.get("text")
    if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != response.get("text_sha256"):
        raise ValueError("parser output digest mismatch")
    return text


def _read(connection: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise ConnectionError("parser worker closed early")
        result.extend(chunk)
    return bytes(result)
