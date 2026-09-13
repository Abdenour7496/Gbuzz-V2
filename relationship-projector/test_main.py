import unittest

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


if __name__ == "__main__":
    unittest.main()
