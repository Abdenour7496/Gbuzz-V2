import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "alert_receiver", Path(__file__).parents[1] / "observability" / "alert-receiver" / "server.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AlertReceiverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "state.json"
        self.state_patch = patch.object(MODULE, "STATE_PATH", self.state)
        self.state_patch.start()
        self.sent = []

    def tearDown(self):
        self.state_patch.stop()
        self.temp.cleanup()

    def publish(self, content, fingerprint):
        self.sent.append((content, fingerprint))
        return f"event-{len(self.sent)}"

    @staticmethod
    def payload(status="firing", severity="critical"):
        return {"status": status, "alerts": [{
            "status": status, "fingerprint": "abc", "startsAt": "2026-09-15T00:00:00Z",
            "labels": {"alertname": "Synthetic", "severity": severity, "job": "probe"},
            "annotations": {"summary": "Synthetic alert", "description": "value 1 > threshold 0"},
        }]}

    def test_fire_duplicate_repeat_and_single_resolve(self):
        self.assertEqual(1, MODULE.process_payload(self.payload(), self.publish, 1000)["delivered"])
        self.assertEqual(1, MODULE.process_payload(self.payload(), self.publish, 1001)["suppressed"])
        self.assertEqual(1, MODULE.process_payload(self.payload(), self.publish, 2800)["delivered"])
        self.assertEqual(1, MODULE.process_payload(self.payload("resolved"), self.publish, 2900)["delivered"])
        self.assertEqual(1, MODULE.process_payload(self.payload("resolved"), self.publish, 2901)["suppressed"])
        self.assertEqual(3, len(self.sent))
        self.assertIn("RESOLVED", self.sent[-1][0])

    def test_warning_repeat_is_four_hours(self):
        alert = self.payload(severity="warning")
        MODULE.process_payload(alert, self.publish, 1000)
        self.assertEqual(1, MODULE.process_payload(alert, self.publish, 1000 + 14399)["suppressed"])
        self.assertEqual(1, MODULE.process_payload(alert, self.publish, 1000 + 14400)["delivered"])

    def test_failed_publish_never_marks_delivered(self):
        with self.assertRaises(RuntimeError):
            MODULE.process_payload(self.payload(), lambda *_: (_ for _ in ()).throw(RuntimeError("relay down")), 1000)
        self.assertFalse(self.state.exists())

    def test_delivery_retry_is_bounded(self):
        attempts=[]
        with patch.object(MODULE,"_publish",side_effect=lambda *_:(attempts.append(1),(_ for _ in ()).throw(RuntimeError()))[1]),patch.object(MODULE.time,"sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError,"bounded retries"):
                MODULE._publish_with_retry("alert","abc")
        self.assertEqual(3,len(attempts))
        self.assertEqual(2,sleep.call_count)

    def test_audit_state_contains_no_alert_payload_or_secret(self):
        MODULE.process_payload(self.payload(), self.publish, 1000)
        state = json.loads(self.state.read_text())
        self.assertEqual(["abc"], list(state))
        self.assertNotIn("Synthetic alert", self.state.read_text())

    def test_authenticated_relay_retries_event_after_auth(self):
        class Relay:
            def __init__(self): self.sent=[]; self.responses=[["AUTH", "challenge"]]
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def send(self, raw):
                message=json.loads(raw); self.sent.append(message)
                if message[0] == "AUTH": self.responses.append(["OK", message[1]["id"], True, ""])
                elif len(self.sent) > 2: self.responses.append(["OK", message[1]["id"], True, ""])
            def recv(self, timeout=None): return json.dumps(self.responses.pop(0))
        relay=Relay()
        with tempfile.NamedTemporaryFile("w", delete=False) as key_file:
            key_file.write("1" * 64); key_path=key_file.name
        environment={
            "ALERT_BUZZ_PRIVATE_KEY_FILE":key_path,
            "ALERT_BUZZ_RELAY_URL":"ws://relay:3000",
            "ALERT_BUZZ_CHANNEL_ID":"channel",
            "ALERT_BUZZ_THREAD_ROOT":"a" * 64,
        }
        try:
            with patch.object(MODULE,"connect",return_value=relay),patch.dict("os.environ",environment,clear=False):
                event_id=MODULE._publish("test","fingerprint")
            self.assertEqual(["EVENT","AUTH","EVENT"],[item[0] for item in relay.sent])
            self.assertEqual(relay.sent[-1][1]["id"],event_id)
        finally:
            Path(key_path).unlink()
