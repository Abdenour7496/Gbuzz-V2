import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "evaluation", Path(__file__).resolve().parents[1] / "scripts" / "evaluate-knowledge.py"
)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)

HASH = "a" * 64


def citation(document_id="a", channel_id="channel-a", ordinal=0):
    return {
        "document_id": document_id,
        "chunk_ordinal": ordinal,
        "document_sha256": HASH,
        "chunk_sha256": HASH,
        "channel_id": channel_id,
        "lifecycle_state": "approved",
    }


class Evaluation(unittest.TestCase):
    def test_forbidden_content_in_graph_is_caught(self):
        result = evaluation.assess(
            {"id": "q", "query": "q", "channel_id": "channel-a", "forbidden_strings": ["private fact"]},
            {"answer": "No matching knowledge found", "graph_nodes": [{"content": "PRIVATE FACT"}]},
        )
        self.assertFalse(result["passed"])

    def test_missing_source_and_invalid_citation_fail(self):
        result = evaluation.assess(
            {"id": "q", "query": "q", "channel_id": "channel-a", "required_document_ids": ["a"]},
            {"answer": "claim [2]", "citations": [citation("b")], "chunks": [{"document_id": "b"}]},
        )
        self.assertEqual(result["recall"], 0)
        self.assertFalse(result["passed"])

    def test_expected_authoritative_evidence_passes(self):
        result = evaluation.assess(
            {"id": "q", "query": "q", "channel_id": "channel-a", "required_document_ids": ["a"]},
            {"answer": "claim [1]", "citations": [citation()], "chunks": [{"document_id": "a"}]},
        )
        self.assertTrue(result["passed"])

    def test_wrong_channel_or_missing_hash_fails_authority(self):
        wrong_channel = citation(channel_id="channel-b")
        missing_hash = citation()
        missing_hash.pop("chunk_sha256")
        case = {"id": "q", "query": "q", "channel_id": "channel-a"}
        for candidate in (wrong_channel, missing_hash):
            with self.subTest(candidate=candidate):
                result = evaluation.assess(
                    case, {"answer": "claim [1]", "citations": [candidate], "chunks": []}
                )
                self.assertFalse(result["citations_authoritative"])
                self.assertFalse(result["passed"])

    def test_all_returned_citations_must_be_referenced(self):
        result = evaluation.assess(
            {"id": "q", "query": "q", "channel_id": "channel-a"},
            {"answer": "claim [1]", "citations": [citation("a", ordinal=0), citation("b", ordinal=1)]},
        )
        self.assertFalse(result["references_valid"])

    def test_expected_no_answer_requires_safe_empty_abstention(self):
        case = {"id": "q", "query": "q", "channel_id": "channel-a", "expect_no_answer": True}
        safe = evaluation.assess(case, {"answer": "Insufficient evidence to answer this question."})
        unsafe = evaluation.assess(case, {"answer": "The answer is probably 42."})
        cited = evaluation.assess(
            case, {"answer": "Insufficient evidence [1]", "citations": [citation()]}
        )
        self.assertTrue(safe["passed"])
        self.assertFalse(unsafe["passed"])
        self.assertFalse(cited["passed"])

    def test_case_contract_is_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "channel_id"):
            evaluation.validate_case({"id": "q", "query": "q"})
        with self.assertRaisesRegex(ValueError, "min_recall"):
            evaluation.validate_case(
                {"id": "q", "query": "q", "channel_id": "channel-a", "min_recall": 1.1}
            )

    def test_summary_exposes_release_metrics(self):
        rows = [
            {
                "passed": True,
                "references_valid": True,
                "citations_authoritative": True,
                "forbidden_content": False,
                "expected_abstention": True,
                "abstained": True,
            }
        ]
        report = evaluation.summarize(rows, p95=1.2, max_p95_seconds=2)
        self.assertTrue(report["passed"])
        self.assertEqual(report["citation_integrity_rate"], 1)
        self.assertEqual(report["abstention_pass_rate"], 1)


if __name__ == "__main__":
    unittest.main()
