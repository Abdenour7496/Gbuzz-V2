import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [((byte / 255.0) * 2.0) - 1.0 for byte in digest[:8]]
    magnitude = sum(value * value for value in values) ** 0.5 or 1.0
    return [value / magnitude for value in values]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond({"models": []})

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/embeddings":
            self.respond({"embedding": vector(str(body.get("prompt", "")))})
        elif self.path == "/v1/embeddings":
            inputs = body.get("input", [])
            if isinstance(inputs, str):
                inputs = [inputs]
            self.respond({"data": [{"embedding": vector(str(item))} for item in inputs]})
        elif self.path in ("/api/generate", "/v1/chat/completions"):
            self.respond({"response": "Grounded integration response.", "choices": [{"message": {"content": "Grounded integration response."}}]})
        else:
            self.send_error(404)

    def respond(self, payload):
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_):
        return


ThreadingHTTPServer(("0.0.0.0", 11434), Handler).serve_forever()
