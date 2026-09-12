#!/usr/bin/env python3
"""Validate governance definitions and derive certification only from bound evidence."""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "OWNER_SELECTION_REQUIRED"


def load(path):
    candidate = Path(path)
    if not candidate.is_absolute(): candidate = ROOT / candidate
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError(f"duplicate_json_key:{key}")
            result[key] = value
        return result
    with candidate.open(encoding="utf-8") as handle: return json.load(handle, object_pairs_hook=unique_object)


def schema_errors(instance, schema, prefix):
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [f"{prefix}:{'/'.join(map(str, error.absolute_path))}:{error.message}" for error in validator.iter_errors(instance)]


def validate_policy_registry(policies):
    errors, seen = [], {}
    schema = load("schemas/governance-policy.schema.json")
    for index, policy in enumerate(policies):
        errors.extend(schema_errors(policy, schema, f"policy[{index}]"))
        key = (policy.get("policy_id"), policy.get("policy_version"))
        canonical = json.dumps(policy, sort_keys=True, separators=(",", ":"))
        if key in seen:
            errors.append(f"policy[{index}]:duplicate_immutable_policy_id_version")
            if seen[key] != canonical: errors.append(f"policy[{index}]:immutable_policy_version_conflict")
        seen[key] = canonical
        if policy.get("status") == "active" and PLACEHOLDER in canonical:
            errors.append(f"policy[{index}]:active_policy_contains_placeholder")
        supersedes = policy.get("supersedes")
        if supersedes is not None:
            expected_prefix = policy.get("policy_id", "") + "@"
            if not supersedes.startswith(expected_prefix): errors.append(f"policy[{index}]:supersedes_different_policy_id")
            else:
                try: prior = int(supersedes.rsplit("@", 1)[1])
                except ValueError: prior = -1
                if prior >= policy.get("policy_version", 0): errors.append(f"policy[{index}]:supersedes_must_be_earlier_version")
    return errors


def validate_structure(fixtures=None, matrix=None, machine=None):
    errors = validate_policy_registry([load("governance/default-policy.template.json")])
    matrix = matrix or load("governance/role-authority-matrix.json")
    roles = matrix.get("roles", [])
    if len(roles) != len(set(roles)) or any(not role for role in roles): errors.append("authority:roles_must_be_unique")
    role_set = set(roles)
    actions = matrix.get("actions", {})
    if len(actions) != len(set(actions)): errors.append("authority:actions_must_be_unique")
    if matrix.get("default") != "deny": errors.append("authority:default_must_deny")
    for action, allowed in actions.items():
        if len(allowed) != len(set(allowed)): errors.append(f"authority:{action}:duplicate_roles")
        if set(allowed) - role_set: errors.append(f"authority:{action}:unknown_roles")
    for action in matrix.get("constraints", {}).get("human_only", []):
        if action not in actions: errors.append(f"authority:{action}:unknown_human_only_action")
        elif "agent" in actions[action]: errors.append(f"authority:{action}:agent_allowed")

    machine = machine or load("governance/lifecycle-state-machine.json")
    states = machine.get("states", [])
    if len(states) != len(set(states)): errors.append("lifecycle:states_must_be_unique")
    state_set = set(states)
    transitions = machine.get("transitions", [])
    transition_keys = [(item.get("from"), item.get("to"), item.get("action")) for item in transitions]
    if len(transition_keys) != len(set(transition_keys)): errors.append("lifecycle:transitions_must_be_unique")
    if machine.get("initial_state") not in state_set: errors.append("lifecycle:invalid_initial_state")
    for item in transitions:
        if item.get("from") not in state_set or item.get("to") not in state_set: errors.append("lifecycle:transition_unknown_state")
    invariants = machine.get("invariants", [])
    if len(invariants) != len(set(invariants)): errors.append("lifecycle:invariants_must_be_unique")
    for required in ("archived_is_not_deleted", "agent_cannot_execute_human_only_transition", "legal_hold_blocks_every_disposition_route", "disposed_requires_reconciled_certificate"):
        if required not in invariants: errors.append(f"lifecycle:missing_invariant:{required}")

    fixtures = fixtures or load("governance/compliance-fixtures.json")
    if "capability_status" in fixtures: errors.append("fixtures:manual_capability_status_forbidden")
    cases = fixtures.get("cases", [])
    ids, families = [case.get("id") for case in cases], [case.get("family") for case in cases]
    if len(cases) != 12: errors.append("fixtures:exactly_12_families_required")
    if len(ids) != len(set(ids)) or any(not value for value in ids): errors.append("fixtures:case_ids_must_be_unique")
    if len(families) != len(set(families)) or any(not value for value in families): errors.append("fixtures:families_must_be_unique")
    for case in cases:
        assertions = case.get("assertions", [])
        if not assertions: errors.append(f"fixtures:{case.get('id')}:assertions_required")
        if len(assertions) != len(set(assertions)): errors.append(f"fixtures:{case.get('id')}:assertions_must_be_unique")
    return errors, fixtures


def current_commit():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def approval_digest(item):
    approval = item["approval"]
    bound = {key: item[key] for key in ("test_id", "result", "artifact_sha256", "code_commit", "policy_id", "policy_version", "governance_schema_version", "environment_profile_sha256", "run_at")}
    bound.update(reviewer_pubkey=approval["reviewer_pubkey"], approved_at=approval["approved_at"])
    return hashlib.sha256(json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify(fixtures, evidence, expected_commit, artifact_root, now=None):
    errors = schema_errors(evidence, load("schemas/governance-evidence.schema.json"), "evidence")
    now = now or datetime.now(timezone.utc)
    def stamp(value): return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if evidence.get("code_commit") != expected_commit: errors.append("evidence:stale_or_mismatched_code_commit")
    try:
        started, completed, valid_until = stamp(evidence["run_started_at"]), stamp(evidence["run_completed_at"]), stamp(evidence["valid_until"])
        if completed < started: errors.append("evidence:run_completed_before_start")
        if valid_until <= completed or valid_until <= now: errors.append("evidence:expired_or_invalid_validity")
    except (KeyError, TypeError, ValueError): errors.append("evidence:invalid_timeline")
    entries, by_id = evidence.get("evidence", []), {}
    for item in entries:
        test_id = item.get("test_id")
        if test_id in by_id: errors.append(f"evidence:{test_id}:duplicate")
        by_id[test_id] = item
        try:
            artifact = (Path(artifact_root) / item["artifact_path"]).resolve()
            root = Path(artifact_root).resolve()
            if root not in artifact.parents and artifact != root: errors.append(f"evidence:{test_id}:artifact_path_escape")
            elif not artifact.is_file(): errors.append(f"evidence:{test_id}:artifact_missing")
            elif hashlib.sha256(artifact.read_bytes()).hexdigest() != item.get("artifact_sha256"): errors.append(f"evidence:{test_id}:artifact_digest_mismatch")
        except (KeyError, OSError, ValueError): errors.append(f"evidence:{test_id}:artifact_unreadable")
        for field in ("code_commit", "policy_id", "policy_version", "governance_schema_version", "environment_profile_sha256"):
            if item.get(field) != evidence.get(field): errors.append(f"evidence:{test_id}:{field}_mismatch")
        try:
            if stamp(item["run_at"]) < stamp(evidence["run_started_at"]) or stamp(item["run_at"]) > stamp(evidence["run_completed_at"]):
                errors.append(f"evidence:{test_id}:run_at_outside_run")
        except (KeyError, TypeError, ValueError): errors.append(f"evidence:{test_id}:invalid_run_at")
    missing = []
    for case in fixtures["cases"]:
        if not case.get("required"): continue
        for assertion in case["assertions"]:
            test_id = f'{case["id"]}/{assertion}'
            item = by_id.get(test_id)
            if item is None: missing.append(test_id); continue
            if case.get("human_evidence_required"):
                if "approval" not in item: errors.append(f"evidence:{test_id}:human_approval_missing")
                else:
                    try:
                        if item["approval"].get("decision_sha256") != approval_digest(item): errors.append(f"evidence:{test_id}:human_approval_binding_mismatch")
                        approved_at = stamp(item["approval"]["approved_at"])
                        if approved_at < stamp(evidence["run_completed_at"]) or approved_at >= stamp(evidence["valid_until"]): errors.append(f"evidence:{test_id}:human_approval_time_invalid")
                    except (KeyError, TypeError, ValueError): errors.append(f"evidence:{test_id}:human_approval_invalid")
    extra = sorted(set(by_id) - {f'{case["id"]}/{assertion}' for case in fixtures["cases"] for assertion in case["assertions"]})
    if extra: errors.append("evidence:unknown_test_ids:" + ",".join(extra))
    if missing: errors.append("evidence:missing_assertions:" + ",".join(missing))
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true", help="validate package structure only")
    parser.add_argument("--evidence", help="certification evidence JSON; required for a passing release gate")
    parser.add_argument("--policy-registry", help="optional JSON array used to check unique immutable policy versions")
    parser.add_argument("--code-commit", help="expected 64-character Git commit; defaults to current HEAD")
    args = parser.parse_args()
    errors, fixtures = validate_structure()
    if args.policy_registry:
        registry = load(args.policy_registry)
        if not isinstance(registry, list): errors.append("policy_registry:array_required")
        else: errors.extend(validate_policy_registry(registry))
    if errors:
        print(json.dumps({"result": "invalid", "errors": sorted(set(errors))}, sort_keys=True)); return 1
    if args.validate:
        print(json.dumps({"result": "valid", "families": len(fixtures["cases"])}, sort_keys=True)); return 0
    if not args.evidence:
        print(json.dumps({"result": "fail_closed", "reason": "machine_readable_evidence_required", "required_assertions": sum(len(case["assertions"]) for case in fixtures["cases"] if case.get("required"))}, sort_keys=True)); return 2
    expected_commit = args.code_commit or current_commit()
    if len(expected_commit) not in (40, 64) or any(ch not in "0123456789abcdef" for ch in expected_commit):
        print(json.dumps({"result": "invalid", "errors": ["expected_commit:git_oid_required"]}, sort_keys=True)); return 1
    evidence_path = Path(args.evidence).resolve()
    evidence_errors = certify(fixtures, load(evidence_path), expected_commit, evidence_path.parent)
    if evidence_errors:
        print(json.dumps({"result": "fail_closed", "evidence_errors": sorted(set(evidence_errors))}, sort_keys=True)); return 2
    print(json.dumps({"result": "pass", "families": len(fixtures["cases"]), "code_commit": expected_commit}, sort_keys=True)); return 0


if __name__ == "__main__": sys.exit(main())
