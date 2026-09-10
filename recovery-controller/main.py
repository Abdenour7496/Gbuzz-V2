import http.client
import json
import os
import socket
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote


LABEL_PREFIX = "com.gbuzz.recovery."


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 10):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class DockerClient:
    """Docker Engine API over a Unix socket or, preferably, a filtering TCP proxy.

    ``endpoint`` is a socket path (``/var/run/docker.sock``) or an ``http://host:port``
    URL such as the docker-socket-proxy service. Only ``GET /containers/*`` and
    ``POST /containers/{id}/restart`` are ever issued, so the proxy can be configured
    with CONTAINERS=1 ALLOW_RESTARTS=1 POST=0 and nothing else.
    """

    def __init__(self, endpoint: str = "/var/run/docker.sock"):
        self.endpoint = endpoint
        self.socket_path = endpoint  # backwards compatibility for callers/tests

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        if self.endpoint.startswith("http://"):
            host = self.endpoint[len("http://"):].rstrip("/")
            return http.client.HTTPConnection(host, timeout=timeout)
        if self.endpoint.startswith("tcp://"):
            host = self.endpoint[len("tcp://"):].rstrip("/")
            return http.client.HTTPConnection(host, timeout=timeout)
        return UnixHTTPConnection(self.endpoint, timeout=timeout)

    def request(self, method: str, path: str, timeout: float = 10) -> Any:
        connection = self._connection(timeout)
        try:
            connection.request(method, path, headers={"Host": "localhost"})
            response = connection.getresponse()
            body = response.read()
            if response.status >= 300:
                raise RuntimeError(f"Docker API {method} {path} returned {response.status}: {body[:300]!r}")
            return json.loads(body) if body else None
        finally:
            connection.close()

    def managed_containers(self, stack_id: str) -> list[dict[str, Any]]:
        filters = json.dumps({"label": [f"{LABEL_PREFIX}enabled=true", f"{LABEL_PREFIX}stack={stack_id}"]})
        containers = self.request("GET", f"/containers/json?all=1&filters={quote(filters)}")
        return [self.request("GET", f"/containers/{item['Id']}/json") for item in containers]

    def restart(self, container_id: str, timeout_seconds: int) -> None:
        # Docker may legitimately use the full stop-grace timeout before
        # killing and restarting a container. Keep the client deadline beyond
        # that server-side timeout so successful restarts are not audited as
        # transport failures.
        self.request(
            "POST",
            f"/containers/{container_id}/restart?t={timeout_seconds}",
            timeout=timeout_seconds + 5,
        )


@dataclass
class ServiceStatus:
    service: str
    container_id: str
    state: str
    health: str
    action: str
    consecutive_failures: int
    last_observed_at: float
    last_action_at: float | None = None
    last_error: str | None = None

    @property
    def healthy(self) -> bool:
        return self.state == "running" and self.health in {"healthy", "none"}


class RecoveryPolicy:
    def __init__(self, failure_threshold: int, cooldown_seconds: int, max_actions: int, window_seconds: int):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.max_actions = max_actions
        self.window_seconds = window_seconds
        self.failures: dict[str, int] = defaultdict(int)
        self.last_action: dict[str, float] = {}
        self.actions: deque[float] = deque()

    def observe(self, service: str, healthy: bool) -> int:
        self.failures[service] = 0 if healthy else self.failures[service] + 1
        return self.failures[service]

    def decision(self, service: str, action: str, now: float) -> tuple[bool, str]:
        while self.actions and self.actions[0] <= now - self.window_seconds:
            self.actions.popleft()
        if action != "restart":
            return False, "monitor_only"
        if self.failures[service] < self.failure_threshold:
            return False, "failure_threshold_not_reached"
        if now - self.last_action.get(service, 0) < self.cooldown_seconds:
            return False, "cooldown"
        if len(self.actions) >= self.max_actions:
            return False, "action_budget_exhausted"
        return True, "restart"

    def record_action(self, service: str, now: float) -> None:
        self.last_action[service] = now
        self.actions.append(now)
        self.failures[service] = 0

    def restore_action(self, service: str, occurred_at: float, now: float) -> None:
        self.last_action[service] = max(occurred_at, self.last_action.get(service, 0))
        if occurred_at > now - self.window_seconds:
            self.actions.append(occurred_at)

    def configuration(self) -> dict[str, int]:
        return {
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "max_actions": self.max_actions,
            "window_seconds": self.window_seconds,
            "actions_in_current_window": len(self.actions),
        }


class Controller:
    def __init__(self, docker: DockerClient, policy: RecoveryPolicy, stack_id: str, audit_path: Path, restart_timeout: int):
        self.docker = docker
        self.policy = policy
        self.stack_id = stack_id
        self.audit_path = audit_path
        self.restart_timeout = restart_timeout
        self.statuses: dict[str, ServiceStatus] = {}
        self.counters: dict[tuple[str, str], int] = defaultdict(int)
        self.last_poll_at = 0.0
        self.last_poll_error: str | None = None
        self.lock = threading.Lock()
        self.restore_policy_state()

    def restore_policy_state(self) -> None:
        if not self.audit_path.exists():
            return
        now = time.time()
        try:
            with self.audit_path.open("r", encoding="utf-8") as source:
                for line in source:
                    event = json.loads(line)
                    if event.get("event") == "remediation" and event.get("action") == "restart" and event.get("result") == "success":
                        self.policy.restore_action(event["service"], float(event["timestamp"]), now)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            self.last_poll_error = f"could not restore recovery audit state: {error}"

    def audit(self, event: dict[str, Any]) -> None:
        event = {"timestamp": time.time(), **event}
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")

    def poll(self, now: float | None = None) -> None:
        now = now or time.time()
        try:
            containers = self.docker.managed_containers(self.stack_id)
            seen: set[str] = set()
            for container in containers:
                labels = container.get("Config", {}).get("Labels") or {}
                service = labels.get(f"{LABEL_PREFIX}service") or labels.get("com.docker.compose.service") or container["Name"].lstrip("/")
                action = labels.get(f"{LABEL_PREFIX}action", "monitor")
                state_data = container.get("State", {})
                state = state_data.get("Status", "unknown")
                health = (state_data.get("Health") or {}).get("Status", "none")
                healthy = state == "running" and health in {"healthy", "none"}
                # Compose creates containers before their dependencies are ready.
                # Starting them here bypasses that ordering and consumes the restart budget.
                initializing = state == "created" or (state == "running" and health == "starting")
                failures = self.policy.observe(service, healthy or initializing)
                status = ServiceStatus(service, container["Id"][:12], state, health, action, failures, now, self.policy.last_action.get(service))
                allowed, reason = (False, "initializing") if initializing else self.policy.decision(service, action, now)
                if not healthy and failures == self.policy.failure_threshold:
                    self.audit({"event": "failure_threshold_reached", "service": service, "state": state, "health": health, "action": action})
                if allowed:
                    try:
                        self.docker.restart(container["Id"], self.restart_timeout)
                        self.policy.record_action(service, now)
                        status.last_action_at = now
                        status.consecutive_failures = 0
                        self.counters[(service, "success")] += 1
                        self.audit({"event": "remediation", "service": service, "action": "restart", "result": "success", "container_id": container["Id"][:12]})
                    except Exception as error:
                        status.last_error = str(error)
                        self.counters[(service, "failure")] += 1
                        self.audit({"event": "remediation", "service": service, "action": "restart", "result": "failure", "error": str(error)})
                elif not healthy and reason in {"monitor_only", "action_budget_exhausted"} and failures == self.policy.failure_threshold:
                    self.audit({"event": "escalation", "service": service, "reason": reason, "state": state, "health": health})
                self.statuses[service] = status
                seen.add(service)
            for missing in set(self.statuses) - seen:
                old = self.statuses[missing]
                failures = self.policy.observe(missing, False)
                self.statuses[missing] = ServiceStatus(missing, old.container_id, "missing", "none", old.action, failures, now, old.last_action_at, "container not returned by Docker")
            self.last_poll_at = now
            self.last_poll_error = None
        except Exception as error:
            self.last_poll_at = now
            self.last_poll_error = str(error)
            self.counters[("controller", "poll_failure")] += 1
            self.audit({"event": "poll_failure", "error": str(error)})

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "status": "ok" if self.last_poll_error is None else "degraded",
                "stack_id": self.stack_id,
                "policy": self.policy.configuration(),
                "last_poll_at": self.last_poll_at,
                "last_poll_error": self.last_poll_error,
                "services": [asdict(item) | {"healthy": item.healthy} for item in sorted(self.statuses.values(), key=lambda value: value.service)],
            }

    def metrics(self) -> str:
        with self.lock:
            return self._metrics_unlocked()

    def _metrics_unlocked(self) -> str:
        lines = [
            "# HELP gbuzz_recovery_controller_up Whether the last Docker poll succeeded.",
            "# TYPE gbuzz_recovery_controller_up gauge",
            f"gbuzz_recovery_controller_up {1 if self.last_poll_error is None else 0}",
            "# HELP gbuzz_recovery_service_healthy Whether a managed service is healthy.",
            "# TYPE gbuzz_recovery_service_healthy gauge",
        ]
        for status in sorted(self.statuses.values(), key=lambda value: value.service):
            service = status.service.replace('\\', '\\\\').replace('"', '\\"')
            lines.append(f'gbuzz_recovery_service_healthy{{service="{service}",action="{status.action}"}} {1 if status.healthy else 0}')
            lines.append(f'gbuzz_recovery_consecutive_failures{{service="{service}"}} {status.consecutive_failures}')
        lines.extend(["# HELP gbuzz_recovery_actions_total Recovery controller actions.", "# TYPE gbuzz_recovery_actions_total counter"])
        for (service, result), count in sorted(self.counters.items()):
            lines.append(f'gbuzz_recovery_actions_total{{service="{service}",result="{result}"}} {count}')
        return "\n".join(lines) + "\n"


def handler_for(controller: Controller) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                snapshot = controller.snapshot()
                self.respond(200 if snapshot["status"] == "ok" else 503, {"status": snapshot["status"]})
            elif self.path == "/api/status":
                self.respond(200, controller.snapshot())
            elif self.path == "/metrics":
                body = controller.metrics().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.respond(404, {"error": "not_found"})

        def respond(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def main() -> None:
    policy = RecoveryPolicy(
        positive_int("RECOVERY_FAILURE_THRESHOLD", 3),
        positive_int("RECOVERY_COOLDOWN_SECONDS", 300),
        positive_int("RECOVERY_MAX_ACTIONS_PER_WINDOW", 3),
        positive_int("RECOVERY_ACTION_WINDOW_SECONDS", 3600),
    )
    controller = Controller(
        DockerClient(os.getenv("DOCKER_HOST") or os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")), policy,
        os.getenv("RECOVERY_STACK_ID", "gbuzz"), Path(os.getenv("RECOVERY_AUDIT_PATH", "/var/lib/gbuzz-recovery/audit.jsonl")),
        positive_int("RECOVERY_RESTART_TIMEOUT_SECONDS", 20),
    )
    server = ThreadingHTTPServer(("0.0.0.0", positive_int("RECOVERY_HTTP_PORT", 8080)), handler_for(controller))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    poll_seconds = positive_int("RECOVERY_POLL_SECONDS", 15)
    while True:
        with controller.lock:
            controller.poll()
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
