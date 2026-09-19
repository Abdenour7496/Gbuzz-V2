import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('evaluation',Path(__file__).resolve().parents[1]/'scripts'/'evaluate-knowledge.py')
evaluation=importlib.util.module_from_spec(spec);spec.loader.exec_module(evaluation)


class Evaluation(unittest.TestCase):
    def test_owner_review_is_bound_to_the_exact_answer(self):
        quality_spec=importlib.util.spec_from_file_location('quality',Path(__file__).resolve().parents[1]/'scripts'/'check-knowledge-quality.py')
        quality=importlib.util.module_from_spec(quality_spec);quality_spec.loader.exec_module(quality)
        report={'passed':True,'results':[{'id':'one','response_sha256':'correct'}]}
        review={'id':'one','response_sha256':'wrong','reviewer':'owner','reviewed_at':'2026-09-14','factual_claims':2,'supported_claims':2,'acceptable':True}
        self.assertFalse(quality.assess(report,[review],{'owner'},minimum_cases=1)['passed'])
        review['response_sha256']='correct'
        self.assertTrue(quality.assess(report,[review],{'owner'},minimum_cases=1)['passed'])
        self.assertFalse(quality.assess(report,[review],{'owner'})['passed'])
    def test_forbidden_content_in_graph_is_caught(self):
        result=evaluation.assess({'id':'q','forbidden_strings':['private fact']},{'answer':'No evidence','graph_nodes':[{'content':'PRIVATE FACT'}]})
        self.assertFalse(result['passed'])

    def test_missing_source_and_invalid_citation_fail(self):
        result=evaluation.assess({'id':'q','required_document_ids':['a']},{'answer':'claim [2]','citations':[{'document_id':'b'}],'chunks':[{'document_id':'b'}]})
        self.assertEqual(result['recall'],0)
        self.assertFalse(result['passed'])

    def test_expected_evidence_passes(self):
        self.assertTrue(evaluation.assess({'id':'q','required_document_ids':['a']},{'answer':'claim [1]','citations':[{'document_id':'a'}],'chunks':[{'document_id':'a'}]})['passed'])
