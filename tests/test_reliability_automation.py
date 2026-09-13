from pathlib import Path
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


class ReliabilityAutomationTests(unittest.TestCase):
    def test_windows_endpoint_observability_is_opt_in_and_provisioned(self):
        prometheus = text("observability/prometheus.yml")
        base = text("docker-compose.observability.yml")
        endpoint = text("docker-compose.endpoint-observability.yml")
        alerts = text("observability/alerts.yml")
        dashboard = json.loads(text("observability/grafana/dashboards/windows-endpoint-health.json"))

        self.assertIn('job_name: windows', prometheus)
        self.assertIn('windows-disabled.yml:/etc/prometheus/targets/windows.yml:ro', base)
        self.assertIn('windows-sa-homelab.yml:/etc/prometheus/targets/windows.yml:ro', endpoint)
        self.assertEqual([], __import__('yaml').safe_load(text("observability/targets/windows-disabled.yml")))
        for name in ("WindowsEndpointCollectorDown", "WindowsEndpointDiskCapacityLow", "WindowsEndpointMemoryPressure", "WindowsEndpointCpuPressure"):
            self.assertIn(name, alerts)
        self.assertEqual("windows-endpoint-health", dashboard["uid"])
        self.assertGreaterEqual(len(dashboard["panels"]), 10)
        self.assertNotIn("OR on() vector(0)", text("observability/grafana/dashboards/windows-endpoint-health.json"))
        self.assertEqual("127.0.0.1", text(".env.example").split("OBSERVABILITY_BIND_ADDR=", 1)[1].splitlines()[0])

    def test_graphiti_queue_alerts_and_metrics_are_retired_without_schema_deletion(self):
        alerts = text("observability/alerts.yml")
        proxy = text("proxy/main.py")
        migrations = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "migrations").glob("*.sql"))
        self.assertNotIn("GraphitiProjection", alerts)
        self.assertNotIn("gcor_graphiti_projection_entries", proxy)
        self.assertNotIn("DROP TABLE gcor.graphiti_projection", migrations)


    def test_backup_automation_is_encrypted_bounded_and_secret_safe(self):
        script = text("scripts/backup-stack.ps1")
        self.assertNotIn("GBUZZ_BACKUP_KEY_BASE64", script)
        for value in ("-e AWS_SECRET_ACCESS_KEY", "MINIO_ROOT_PASSWORD", "MaxLocalBytes", "prune-backups.ps1", "Protect-BackupFile", "recovery-manifest.json", "postgres-globals.sql", "relay-git.tar"):
            self.assertIn(value, script)
        self.assertIn("ComposePlan.psm1", script)
        self.assertIn("component-consistent", script)
        self.assertIn("check-minio-references.py", script)


    def test_backup_alerts_cover_failure_age_and_missing_telemetry(self):
        alerts = text("observability/alerts.yml")
        for name in ("GbuzzBackupFailed", "GbuzzBackupTooOld", "GbuzzBackupMetricsMissing"):
            self.assertIn(name, alerts)
        self.assertIn("21600", alerts)


    def test_startup_is_bounded_and_exact_overlay_list_excludes_graph(self):
        startup = text("scripts/start-gbuzz-at-logon.ps1")
        config = text("config/windows-startup.compose-files.json")
        self.assertIn("DockerWaitSeconds=300", startup)
        self.assertIn("ReadinessWaitSeconds=300", startup)
        self.assertNotIn("docker-compose.graph", config)
        for name in ("docker-compose.enterprise.yml", "docker-compose.observability.yml", "docker-compose.buzz.yml"):
            self.assertIn(name, config)


    def test_external_transfer_fails_closed(self):
        adapter = text("scripts/offhost-upload.ps1")
        self.assertIn("if(-not $Approved)", adapter)
        self.assertIn("owner supplies and approves", adapter)

    def test_alertmanager_template_repeats_unacknowledged_critical_alerts(self):
        config = text("observability/alertmanager.production.yml.example")
        self.assertIn('severity="critical"', config)
        self.assertIn("repeat_interval: 10m", config)
        self.assertIn("REPLACE_WITH_APPROVED_ON_CALL_WEBHOOK", config)

    def test_minio_export_preserves_versions_and_delete_markers(self):
        spec = importlib.util.spec_from_file_location("backup_minio_versions", ROOT / "scripts/backup-minio-versions.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class Paginator:
            def paginate(self, **_):
                return [{"Versions": [{"Key": "folder/item", "VersionId": "unsafe/slash", "IsLatest": False, "Size": 3, "ETag": '"etag"', "LastModified": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)}],
                         "DeleteMarkers": [{"Key": "gone", "VersionId": "delete-1", "IsLatest": True, "LastModified": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)}]}]

        class Client:
            def list_buckets(self): return {"Buckets": [{"Name": "buzz-media"}]}
            def get_paginator(self, _): return Paginator()
            def get_object(self, **_): return {"Body": io.BytesIO(b"abc")}

        with tempfile.TemporaryDirectory() as folder, patch.object(module.boto3, "client", return_value=Client()), patch.dict(os.environ, {"BACKUP_OUTPUT": folder, "S3_ENDPOINT": "http://minio", "AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "y"}):
            module.main()
            inventory = json.loads((Path(folder) / "inventory.json").read_text())
            self.assertEqual(["buzz-media"], inventory["buckets"])
            self.assertEqual(2, len(inventory["versions"]))
            self.assertTrue(any(item["delete_marker"] for item in inventory["versions"]))
            self.assertEqual(b"abc", (Path(folder) / "blobs" / inventory["versions"][0]["sha256"]).read_bytes())


if __name__ == "__main__":
    unittest.main()
