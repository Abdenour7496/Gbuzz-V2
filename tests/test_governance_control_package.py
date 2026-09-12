import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("governance_gate", ROOT / "scripts/evaluate-governance-controls.py")
gate = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(gate)
HEX = "a" * 40


def read(relative): return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def resolved_policy(version=1):
    policy = read("governance/default-policy.template.json")
    policy.update(status="active", effective_at="2026-09-12T00:00:00Z", policy_id="enterprise-records", policy_version=version)
    if version > 1: policy["supersedes"] = f"enterprise-records@{version - 1}"
    encoded = json.dumps(policy).replace("OWNER_SELECTION_REQUIRED", "records-authority")
    policy = json.loads(encoded)
    for location in (("review", "interval"), ("review", "approval_sla"), ("retention", "period"), ("disposition", "propagation_sla"), ("audit_pack", "retention_period")):
        policy[location[0]][location[1]] = "P1D"
    return policy


def complete_evidence(fixtures, artifact_root, commit=HEX):
    now = datetime.now(timezone.utc)
    artifact = Path(artifact_root) / "test-artifact.json"
    artifact.write_text('{"result":"pass"}\n', encoding="utf-8")
    artifact_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    bundle = {"schema_version": "1.0.0", "governance_schema_version": "1.0.0", "code_commit": commit,
        "policy_id": "enterprise-records", "policy_version": 1, "environment_profile_sha256": "b" * 64,
        "run_started_at": (now - timedelta(minutes=10)).isoformat(), "run_completed_at": (now - timedelta(minutes=1)).isoformat(),
        "valid_until": (now + timedelta(days=1)).isoformat(), "evidence": []}
    for case in fixtures["cases"]:
        for assertion in case["assertions"]:
            item = {"test_id": f'{case["id"]}/{assertion}', "result": "pass", "artifact_path": artifact.name, "artifact_sha256": artifact_digest,
                "code_commit": commit, "policy_id": bundle["policy_id"], "policy_version": 1,
                "governance_schema_version": "1.0.0", "environment_profile_sha256": bundle["environment_profile_sha256"],
                "run_at": (now - timedelta(minutes=2)).isoformat()}
            if case["human_evidence_required"]:
                item["approval"] = {"reviewer_pubkey": "d" * 64, "decision_sha256": "0" * 64, "approved_at": now.isoformat()}
                item["approval"]["decision_sha256"] = gate.approval_digest(item)
            bundle["evidence"].append(item)
    return bundle


class GovernanceControlPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.fixtures = read("governance/compliance-fixtures.json")

    def test_default_policy_is_valid_draft_only_and_selects_no_legal_period(self):
        schema, policy = read("schemas/governance-policy.schema.json"), read("governance/default-policy.template.json")
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(policy)
        self.assertEqual("draft", policy["status"]); self.assertIn("OWNER_SELECTION_REQUIRED", json.dumps(policy))
        self.assertFalse(policy["retention"]["automatic_deletion_allowed"])

    def test_active_policy_rejects_placeholder_and_requires_effective_at(self):
        schema, policy = read("schemas/governance-policy.schema.json"), read("governance/default-policy.template.json")
        policy["status"] = "active"
        errors = gate.schema_errors(policy, schema, "policy")
        self.assertTrue(any("effective_at" in error for error in errors)); self.assertTrue(any("OWNER_SELECTION_REQUIRED" in error for error in errors))

    def test_resolved_active_policy_is_valid(self):
        self.assertEqual([], gate.validate_policy_registry([resolved_policy()]))

    def test_invalid_supersession_and_duplicate_immutable_version_fail(self):
        policy = resolved_policy(2); policy["supersedes"] = "other-policy@1"
        errors = gate.validate_policy_registry([policy, copy.deepcopy(policy)])
        self.assertTrue(any("supersedes_different_policy_id" in error for error in errors))
        self.assertTrue(any("duplicate_immutable_policy_id_version" in error for error in errors))
        policy["supersedes"] = "enterprise-records@2"
        self.assertTrue(any("supersedes_must_be_earlier_version" in error for error in gate.validate_policy_registry([policy])))

    def test_unknown_and_duplicate_roles_fail(self):
        matrix = read("governance/role-authority-matrix.json"); matrix["roles"].append("agent")
        matrix["actions"]["approve_knowledge"].append("unknown-role")
        errors, _ = gate.validate_structure(matrix=matrix)
        self.assertIn("authority:roles_must_be_unique", errors); self.assertIn("authority:approve_knowledge:unknown_roles", errors)

    def test_duplicate_json_action_key_is_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write('{"actions":{"approve":[],"approve":[]}}'); path = handle.name
        try:
            with self.assertRaisesRegex(ValueError, "duplicate_json_key:approve"): gate.load(path)
        finally: Path(path).unlink()

    def test_duplicate_and_unknown_transitions_fail(self):
        machine = read("governance/lifecycle-state-machine.json")
        machine["transitions"].append(copy.deepcopy(machine["transitions"][0]))
        machine["transitions"].append({"from": "unknown", "to": "disposed", "action": "bad", "actor": "agent"})
        errors, _ = gate.validate_structure(machine=machine)
        self.assertIn("lifecycle:transitions_must_be_unique", errors); self.assertIn("lifecycle:transition_unknown_state", errors)

    def test_agent_is_denied_every_human_only_action(self):
        matrix = read("governance/role-authority-matrix.json")
        for action in matrix["constraints"]["human_only"]: self.assertNotIn("agent", matrix["actions"][action], action)
        matrix["actions"][matrix["constraints"]["human_only"][0]].append("agent")
        self.assertTrue(any(error.endswith(":agent_allowed") for error in gate.validate_structure(matrix=matrix)[0]))

    def test_hold_and_disposition_invariant_removal_fails(self):
        machine = read("governance/lifecycle-state-machine.json")
        machine["invariants"].remove("legal_hold_blocks_every_disposition_route")
        machine["invariants"].remove("disposed_requires_reconciled_certificate")
        errors, _ = gate.validate_structure(machine=machine)
        self.assertTrue(any("legal_hold_blocks" in error for error in errors)); self.assertTrue(any("disposed_requires" in error for error in errors))

    def test_exactly_twelve_unique_acceptance_families(self):
        self.assertEqual(12, len(self.fixtures["cases"])); self.assertEqual(12, len({case["id"] for case in self.fixtures["cases"]}))

    def test_structural_validation_passes(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-governance-controls.py"), "--validate"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_release_gate_requires_evidence_not_manual_status(self):
        modified = copy.deepcopy(self.fixtures); modified["capability_status"] = {"manual": "implemented_and_verified"}
        errors, _ = gate.validate_structure(fixtures=modified)
        self.assertIn("fixtures:manual_capability_status_forbidden", errors)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate-governance-controls.py")], capture_output=True, text=True)
        self.assertEqual(2, result.returncode); self.assertEqual("machine_readable_evidence_required", json.loads(result.stdout)["reason"])

    def test_complete_bound_evidence_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual([], gate.certify(self.fixtures, complete_evidence(self.fixtures, folder), HEX, folder))

    def test_evidence_digest_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = complete_evidence(self.fixtures, folder); evidence["evidence"][0]["artifact_sha256"] = "f" * 64
            self.assertTrue(any("artifact_digest_mismatch" in error for error in gate.certify(self.fixtures, evidence, HEX, folder)))

    def test_stale_commit_evidence_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = complete_evidence(self.fixtures, folder)
            self.assertIn("evidence:stale_or_mismatched_code_commit", gate.certify(self.fixtures, evidence, "f" * 40, folder))

    def test_partial_assertion_evidence_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = complete_evidence(self.fixtures, folder); evidence["evidence"].pop()
            self.assertTrue(any("missing_assertions" in error for error in gate.certify(self.fixtures, evidence, HEX, folder)))

    def test_duplicate_assertion_evidence_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = complete_evidence(self.fixtures, folder); evidence["evidence"].append(copy.deepcopy(evidence["evidence"][0]))
            self.assertTrue(any("duplicate" in error for error in gate.certify(self.fixtures, evidence, HEX, folder)))

    def test_required_human_approval_binding_fails_when_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = complete_evidence(self.fixtures, folder); evidence["evidence"][0].pop("approval")
            self.assertTrue(any("human_approval_missing" in error for error in gate.certify(self.fixtures, evidence, HEX, folder)))

    def test_changed_artifact_invalidates_human_approval_binding(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = complete_evidence(self.fixtures, folder)
            evidence["evidence"][0]["artifact_sha256"] = "f" * 64
            errors = gate.certify(self.fixtures, evidence, HEX, folder)
            self.assertTrue(any("artifact_digest_mismatch" in error for error in errors))
            self.assertTrue(any("human_approval_binding_mismatch" in error for error in errors))


if __name__ == "__main__": unittest.main()
