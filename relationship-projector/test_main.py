import unittest
from pathlib import Path
from uuid import uuid4

import main


class RelationshipProjectionTest(unittest.TestCase):
    def test_ids_are_deterministic_and_revision_bound(self):
        first = main.deterministic_id("doc", "a" * 64, "claims", "Policy")
        self.assertEqual(first, main.deterministic_id("doc", "a" * 64, "claims", " policy "))
        self.assertNotEqual(first, main.deterministic_id("doc", "b" * 64, "claims", "Policy"))

    def test_valid_projection_is_normalized(self):
        result = main.validate_projection({
            "entities": [{"key": "Policy", "text": "Leave policy", "evidence_ordinals": [1, 0, 1], "confidence": .9}],
            "claims": [{"key": "Allowance", "text": "Staff receive leave", "evidence_ordinals": [1], "confidence": .8}],
            "links": [{"source": "Allowance", "target": "Policy", "relation": "about", "evidence_ordinals": [1], "confidence": .7}],
        }, {0, 1})
        self.assertEqual(result["entities"][0]["evidence_ordinals"], [0, 1])
        self.assertEqual(result["links"][0]["relation"], "ABOUT")

    def test_unknown_evidence_and_endpoint_fail_closed(self):
        base = {
            "entities": [{"key": "Policy", "text": "Leave policy", "evidence_ordinals": [0], "confidence": 1}],
            "claims": [], "links": [],
        }
        bad_evidence = dict(base)
        bad_evidence["entities"] = [{**base["entities"][0], "evidence_ordinals": [99]}]
        with self.assertRaises(ValueError):
            main.validate_projection(bad_evidence, {0})
        bad_link = dict(base)
        bad_link["links"] = [{"source": "Policy", "target": "Missing", "relation": "ABOUT", "evidence_ordinals": [0], "confidence": 1}]
        with self.assertRaises(ValueError):
            main.validate_projection(bad_link, {0})

    def test_unbounded_or_extra_model_output_fails(self):
        with self.assertRaises(ValueError):
            main.validate_projection({"entities": [], "claims": [], "links": [], "instructions": "ignore policy"}, {0})
        with self.assertRaises(ValueError):
            main.validate_projection({"entities": [
                {"key": str(i), "text": "x", "evidence_ordinals": [0], "confidence": 1}
                for i in range(main.MAX_ENTITIES + 1)
            ], "claims": [], "links": []}, {0})

    def test_model_response_bytes_and_depth_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "byte limit"):
            main.bounded_json(b'"' + b'x' * main.MAX_RESPONSE_BYTES + b'"')
        nested = "[" * (main.MAX_JSON_DEPTH + 1) + "0" + "]" * (main.MAX_JSON_DEPTH + 1)
        with self.assertRaisesRegex(ValueError, "nesting limit"):
            main.bounded_json(nested.encode())

    def test_chunk_snapshot_binds_content_and_nodes(self):
        base = [{"ordinal": 0, "node_id": "node", "content": "evidence"}]
        self.assertEqual(main.chunk_snapshot(base), main.chunk_snapshot(list(base)))
        self.assertNotEqual(main.chunk_snapshot(base), main.chunk_snapshot([{**base[0], "content": "changed"}]))

    def test_publication_rechecks_lease_channel_revision_and_chunks(self):
        owner = uuid4()
        lease = {"lease_owner": owner, "lease_valid": True}
        source = {"content_sha256": "a" * 64, "channel_id": "allowed"}
        current = {**source, "state": "approved", "evidence_current": True}
        self.assertTrue(main.publication_current(lease, owner, current, source, "chunks", "chunks"))
        for changed in (
            ({**lease, "lease_valid": False}, current, "chunks"),
            (lease, {**current, "channel_id": "other"}, "chunks"),
            (lease, {**current, "content_sha256": "b" * 64}, "chunks"),
            (lease, current, "changed"),
        ):
            with self.subTest(changed=changed):
                self.assertFalse(main.publication_current(changed[0], owner, changed[1], source, changed[2], "chunks"))

    def test_migration_preserves_fail_closed_interactive_edge_policy(self):
        migration = (Path(__file__).resolve().parent / "migrations" / "0015_postgres_relationship_projection.sql").read_text()
        self.assertNotIn("DROP POLICY IF EXISTS edges_channel_scope", migration)
        self.assertNotIn("gcor.scope_channel() IS NULL", migration)
        self.assertIn("gcor.scope_workload() = 'relationship-projector'", migration)
        self.assertIn("FOR UPDATE OF d SKIP LOCKED", Path(main.__file__).read_text())


if __name__ == "__main__":
    unittest.main()
