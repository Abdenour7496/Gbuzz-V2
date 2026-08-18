import tempfile
import unittest
from pathlib import Path

import main


def container(service: str, state: str = "running", health: str = "healthy", action: str = "restart"):
    return {
        "Id": f"{service:0<64}",
        "Name": f"/{service}",
        "Config": {"Labels": {
            "com.gbuzz.recovery.service": service,
            "com.gbuzz.recovery.action": action,
        }},
        "State": {"Status": state, "Health": {"Status": health}},
    }


class FakeDocker:
    def __init__(self, containers):
        self.containers = containers
        self.restarts = []

    def managed_containers(self, stack_id):
        return self.containers

    def restart(self, container_id, timeout_seconds):
        self.restarts.append((container_id, timeout_seconds))


class RecoveryPolicyTest(unittest.TestCase):
    def test_requires_threshold_and_resets_after_success(self):
        policy = main.RecoveryPolicy(3, 300, 3, 3600)
        for _ in range(2):
            policy.observe("proxy", False)
            self.assertFalse(policy.decision("proxy", "restart", 1000)[0])
        policy.observe("proxy", False)
        self.assertTrue(policy.decision("proxy", "restart", 1000)[0])
        policy.record_action("proxy", 1000)
        self.assertEqual(0, policy.failures["proxy"])

    def test_cooldown_and_global_budget(self):
        policy = main.RecoveryPolicy(1, 300, 1, 3600)
        policy.observe("a", False)
        policy.record_action("a", 1000)
        policy.observe("a", False)
        self.assertEqual("cooldown", policy.decision("a", "restart", 1100)[1])
        policy.observe("b", False)
        self.assertEqual("action_budget_exhausted", policy.decision("b", "restart", 1100)[1])
        self.assertTrue(policy.decision("b", "restart", 5001)[0])

    def test_monitor_only_never_remediates(self):
        policy = main.RecoveryPolicy(1, 1, 1, 1)
        policy.observe("postgres", False)
        self.assertEqual((False, "monitor_only"), policy.decision("postgres", "monitor", 1000))


class ControllerTest(unittest.TestCase):
    def test_restarts_only_allowlisted_unhealthy_service_and_audits(self):
        docker = FakeDocker([container("proxy", health="unhealthy"), container("postgres", health="unhealthy", action="monitor")])
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            controller = main.Controller(docker, main.RecoveryPolicy(2, 300, 3, 3600), "gbuzz", audit, 20)
            controller.poll(1000)
            self.assertEqual([], docker.restarts)
            controller.poll(1015)
            self.assertEqual(1, len(docker.restarts))
            self.assertIn('"event":"remediation"', audit.read_text())
            self.assertIn('"event":"escalation"', audit.read_text())

    def test_exports_status_and_metrics(self):
        docker = FakeDocker([container("proxy")])
        with tempfile.TemporaryDirectory() as directory:
            controller = main.Controller(docker, main.RecoveryPolicy(3, 300, 3, 3600), "gbuzz", Path(directory) / "audit.jsonl", 20)
            controller.poll(1000)
            self.assertTrue(controller.snapshot()["services"][0]["healthy"])
            self.assertIn('gbuzz_recovery_service_healthy{service="proxy",action="restart"} 1', controller.metrics())

    def test_restores_action_budget_from_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            audit.write_text('{"timestamp":1000,"event":"remediation","service":"proxy","action":"restart","result":"success"}\n')
            policy = main.RecoveryPolicy(1, 300, 1, 3600)
            original_time = main.time.time
            main.time.time = lambda: 1100
            try:
                main.Controller(FakeDocker([]), policy, "gbuzz", audit, 20)
            finally:
                main.time.time = original_time
            policy.observe("other", False)
            self.assertEqual("action_budget_exhausted", policy.decision("other", "restart", 1100)[1])


if __name__ == "__main__":
    unittest.main()
