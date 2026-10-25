import importlib.util
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("judge", root / "scripts" / "judge-answers.py")
judge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(judge)

CHUNKS = [{"content": "The search cluster was down for 336 minutes. Root cause: a bad schema migration."},
          {"content": "Per diem for Sydney is 180 dollars per day."}]


def keyword_judge(prompt):
    # Fake judge: YES when the statement's key number appears in the evidence block.
    evidence, statement = prompt.split("Statement:", 1)
    numbers = [w for w in statement.split() if w.strip(".").isdigit()]
    return "YES" if numbers and all(n.strip(".") in evidence for n in numbers) else "NO"


class Sentences(unittest.TestCase):
    def test_drops_extractive_header_and_reference_only_parts(self):
        answer = "Best available knowledge for: why\n\n[1] Root cause was a migration. [2]"
        self.assertEqual(judge.sentences(answer), ["[1] Root cause was a migration."])

    def test_splits_generated_answer(self):
        parts = judge.sentences("The outage lasted 336 minutes [1]. Per diem is 180 dollars [2].")
        self.assertEqual(len(parts), 2)
        self.assertEqual(judge.cited_indexes(parts[1], 2), [2])
        self.assertEqual(judge.cited_indexes("claim [7]", 2), [])


class Verdicts(unittest.TestCase):
    def test_parse(self):
        self.assertTrue(judge.parse_verdict("Yes."))
        self.assertFalse(judge.parse_verdict(" no"))
        self.assertIsNone(judge.parse_verdict("maybe"))

    def test_prompt_strips_references_and_marks_evidence_untrusted(self):
        prompt = judge.judge_prompt("It took 336 minutes [1].", ["evidence"])
        self.assertIn("Statement: It took 336 minutes .", prompt)
        self.assertIn("untrusted", prompt)


class Assess(unittest.TestCase):
    def test_supported_and_hallucinated_sentences(self):
        response = {"answer": "The outage lasted 336 minutes [1]. Per diem is 250 dollars [2].", "chunks": CHUNKS, "citations": [{}, {}]}
        row = judge.assess_case({"id": "a", "expected_answer_contains": ["336"]}, response, keyword_judge)
        self.assertEqual((row["sentences"], row["judged"], row["supported"]), (2, 2, 1))
        self.assertEqual(row["faithfulness"], 0.5)
        self.assertTrue(row["references_in_bounds"])
        self.assertTrue(row["expected_content_present"])

    def test_out_of_bounds_reference(self):
        row = judge.assess_case({"id": "a"}, {"answer": "It lasted 336 minutes [5].", "chunks": CHUNKS, "citations": [{}]}, keyword_judge)
        self.assertFalse(row["references_in_bounds"])

    def test_abstention(self):
        ok = judge.assess_case({"id": "n", "expect_no_answer": True}, {"answer": "No authorized evidence was found.", "chunks": [], "citations": []}, keyword_judge)
        bad = judge.assess_case({"id": "n", "expect_no_answer": True}, {"answer": "Probably 42.", "chunks": CHUNKS, "citations": [{}]}, keyword_judge)
        self.assertTrue(ok["abstention_ok"])
        self.assertFalse(bad["abstention_ok"])

    def test_summary(self):
        rows = [judge.assess_case({"id": "a"}, {"answer": "The outage lasted 336 minutes [1].", "chunks": CHUNKS, "citations": [{}]}, keyword_judge),
                judge.assess_case({"id": "b"}, {"answer": "Per diem is 250 dollars [2].", "chunks": CHUNKS, "citations": [{}]}, keyword_judge),
                judge.assess_case({"id": "n", "expect_no_answer": True}, {"answer": "", "chunks": [], "citations": []}, keyword_judge),
                {"id": "e", "error": "HTTPError"}]
        summary = judge.summarize(rows)
        self.assertTrue(summary["advisory"])
        self.assertEqual(summary["sentence_faithfulness"], 0.5)
        self.assertEqual(summary["answers_fully_supported"], 0.5)
        self.assertEqual(summary["abstention_rate"], 1.0)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["lowest_faithfulness"][0]["id"], "b")

    def test_judge_must_be_local(self):
        with self.assertRaises(SystemExit):
            judge.ollama_judge("https://api.example.com", "m")
        judge.ollama_judge("http://ollama:11434", "m")


if __name__ == "__main__":
    unittest.main()
