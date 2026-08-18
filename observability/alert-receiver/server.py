import json
import os
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


AUDIT_PATH = Path(os.getenv("ALERT_AUDIT_PATH", "/data/alerts.jsonl"))
PORT = int(os.getenv("PORT", "8080"))
WRITE_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._reply(404, {"error": "not found"})
            return
        self._reply(200, {"status": "ok", "audit_path": str(AUDIT_PATH)})

    def do_POST(self) -> None:
        if self.path != "/alerts":
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_048_576:
                raise ValueError("invalid payload length")
            payload = json.loads(self.rfile.read(length))
            record = {
                "received_at": datetime.now(timezone.utc).isoformat(),
                "status": payload.get("status"),
                "receiver": payload.get("receiver"),
                "alerts": payload.get("alerts", []),
            }
            AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with WRITE_LOCK, AUDIT_PATH.open("a", encoding="utf-8") as audit:
                audit.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._reply(200, {"status": "accepted", "alerts": len(record["alerts"])})
        except (ValueError, json.JSONDecodeError) as error:
            self._reply(400, {"error": str(error)})

    def log_message(self, format: str, *args) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
