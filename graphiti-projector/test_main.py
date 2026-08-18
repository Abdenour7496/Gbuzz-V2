import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import main


class GraphitiProjectorTest(unittest.TestCase):
    def sample_row(self):
        return {
            "entry_id": uuid4(), "session_id": uuid4(), "external_session_id": "session-1",
            "channel_id": "channel-1", "channel_name": "General",
            "participant_id": uuid4(), "participant_type": "document",
            "participant_external_id": "doc-1", "participant_name": "Runbook.pdf",
            "entry_external_id": "message-1", "entry_type": "document",
            "content": "The release is Friday.",
            "occurred_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
            "source_document_id": uuid4(), "source_record_id": uuid4(),
            "graphiti_episode_id": uuid4(), "group_id": str(uuid4()),
            "graphiti_operation": "add",
            "previous_graphiti_episode_id": uuid4(),
            "metadata": {"bucket": "general", "record_key": "bundles/record.json"},
        }

    def test_document_is_encoded_as_session_participant(self):
        body = json.loads(main.build_episode_body(self.sample_row()))
        self.assertEqual("document", body["participant"]["type"])
        self.assertEqual("The release is Friday.", body["entry"]["content"])
        self.assertEqual("bundles/record.json", body["provenance"]["record_key"])

    def test_prefers_current_add_memory_tool(self):
        tools = [SimpleNamespace(name="add_episode", inputSchema={"properties": {}}),
                 SimpleNamespace(name="add_memory", inputSchema={"properties": {"reference_time": {}}})]
        name, accepted = main.choose_ingest_tool(tools)
        self.assertEqual("add_memory", name)
        self.assertIn("reference_time", accepted)

    def test_only_sends_supported_mcp_arguments(self):
        row = self.sample_row()
        args = main.build_tool_arguments(row, {"name", "episode_body", "uuid", "reference_time"})
        self.assertEqual({"name", "episode_body", "reference_time"}, set(args))
        self.assertTrue(args["name"].startswith(f"entry:{row['entry_id']} "))

    def test_requires_upstream_delete_episode_tool(self):
        name, accepted = main.choose_delete_tool([
            SimpleNamespace(name="delete_episode", inputSchema={"properties": {"uuid": {}, "group_id": {}}})
        ])
        self.assertEqual("delete_episode", name)
        self.assertEqual({"uuid", "group_id"}, accepted)

    def test_requires_episode_reconciliation_tool(self):
        name, accepted = main.choose_episode_list_tool([
            SimpleNamespace(name="get_episodes", inputSchema={"properties": {"group_ids": {}}})
        ])
        self.assertEqual("get_episodes", name)
        self.assertEqual({"group_ids"}, accepted)

    def test_unwraps_graphiti_structured_result(self):
        result = SimpleNamespace(
            structuredContent={"result": {"episodes": [{"uuid": "episode-1"}]}},
            content=[],
        )
        self.assertEqual(
            [{"uuid": "episode-1"}],
            main.tool_result_object(result)["episodes"],
        )

    def test_release_gate_projection_buckets(self):
        self.assertEqual("pending", main.projection_bucket("pending", 0, False, 8))
        self.assertEqual("retryable", main.projection_bucket("failed", 7, False, 8))
        self.assertEqual("dead_letter", main.projection_bucket("failed", 8, False, 8))
        self.assertEqual("in_flight", main.projection_bucket("processing", 1, False, 8))
        self.assertEqual("in_flight", main.projection_bucket("submitted", 1, False, 8))
        self.assertIsNone(main.projection_bucket("submitted", 1, True, 8))
        self.assertIsNone(main.projection_bucket("skipped", 0, False, 8))


if __name__ == "__main__":
    unittest.main()
