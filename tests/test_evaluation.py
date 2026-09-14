import importlib.util,json
from pathlib import Path
import unittest

root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("evaluation",root/"scripts"/"evaluate-knowledge.py");evaluation=importlib.util.module_from_spec(spec);spec.loader.exec_module(evaluation)
spec=importlib.util.spec_from_file_location("corpus",root/"scripts"/"new-enterprise-evaluation-corpus.py");corpus=importlib.util.module_from_spec(spec);spec.loader.exec_module(corpus)
SLOS=json.loads(evaluation.DEFAULT_SLOS_PATH.read_text())

def evidence():
    content="supported evidence";chunk=evaluation.digest(content.encode())
    response={"answer":"claim [1]","citations":[{"document_id":"a","source_uri":"buzz://a","chunk_ordinal":1,"document_sha256":"d"*64,"chunk_sha256":chunk}],"chunks":[{"document_id":"a","ordinal":1,"content":content}]}
    resolution={"authorized":True,"channel_id":"chan","knowledge_state":"approved","source_uri":"buzz://a","document_sha256":"d"*64,"chunk_sha256":chunk}
    return response,[resolution]

class Evaluation(unittest.TestCase):
    def test_resolved_integrity_passes(self):
        response,resolutions=evidence();result=evaluation.assess({"id":"q","category":"citation","channel_id":"chan","required_document_ids":["a"]},response,resolutions)
        self.assertTrue(result["technical_passed"])

    def test_unresolved_or_mismatched_citation_fails(self):
        response,resolutions=evidence()
        self.assertFalse(evaluation.assess({"id":"q","category":"citation","channel_id":"chan"},response,[])["technical_passed"])
        resolutions[0]["chunk_sha256"]="0"*64
        self.assertFalse(evaluation.assess({"id":"q","category":"citation","channel_id":"chan"},response,resolutions)["technical_passed"])

    def test_redirects_are_rejected_before_authorization_can_be_forwarded(self):
        self.assertIsNone(evaluation.NoRedirectHandler().redirect_request(None,None,302,"Found",{},"http://other.invalid"))

    def test_archived_revoked_and_cross_channel_evidence_fail(self):
        response,resolutions=evidence()
        for change in ({"knowledge_state":"archived"},{"authorized":False},{"channel_id":"other"}):
            checked=[resolutions[0]|change]
            self.assertFalse(evaluation.assess({"id":"q","category":"citation","channel_id":"chan"},response,checked)["technical_passed"])

    def test_revocation_before_release_denies_result(self):
        response,resolutions=evidence();result=evaluation.assess({"id":"q","category":"revocation","channel_id":"chan"},response,resolutions,release_authorized=False)
        self.assertFalse(result["technical_passed"]);self.assertFalse(result["release_authorized"])

    def test_abstention_requires_approved_form_and_no_facts(self):
        case={"id":"q","category":"abstention","channel_id":"chan","expect_no_answer":True}
        self.assertTrue(evaluation.assess(case,{"answer":"No authorized evidence was found."},[])["technical_passed"])
        self.assertFalse(evaluation.assess(case,{"answer":"Probably 78 hours."},[])["technical_passed"])

    def test_prefilled_support_is_forbidden(self):
        cases=corpus.build();cases[0]["owner_review"]={"supported":True};errors,_=evaluation.validate_cases(cases)
        self.assertTrue(any("owner_review is forbidden" in e for e in errors))

    def test_changed_answer_invalidates_judgment(self):
        response,resolutions=evidence();case={"id":"q","category":"factual-support","channel_id":"chan"};result=evaluation.assess(case,response,resolutions)
        provenance={"model_digest":"sha256:"+"1"*64,"corpus_sha256":"2"*64,"code_commit":"3"*40};record=evaluation.review_record("run",case,response,result,provenance)
        judgment={key:record[key] for key in ("run_id","id","answer_sha256","evidence_sha256","model_digest","corpus_sha256","code_commit")}|{"supported":True}
        self.assertTrue(evaluation.judgment_binding(record,judgment))
        changed=evaluation.review_record("run",case,response|{"answer":"different [1]"},result,provenance)
        self.assertFalse(evaluation.judgment_binding(changed,judgment))

    def test_signed_judgment_is_bound_to_approved_reviewer(self):
        try:from coincurve import PrivateKey
        except ImportError:self.skipTest("canonical application environment supplies coincurve")
        response,resolutions=evidence();case={"id":"q","category":"factual-support","channel_id":"chan"};result=evaluation.assess(case,response,resolutions);provenance={"model_digest":"m","corpus_sha256":"c","code_commit":"g"};record=evaluation.review_record("run",case,response,result,provenance);key=PrivateKey();pub=key.public_key_xonly.format().hex()
        judgment={k:record[k] for k in ("run_id","id","answer_sha256","evidence_sha256","model_digest","corpus_sha256","code_commit")}|{"supported":True,"reviewer_pubkey":pub};judgment["signature"]=key.sign_schnorr(bytes.fromhex(evaluation.digest(judgment))).hex()
        self.assertTrue(evaluation.verify_judgment(record,judgment,{pub}));judgment["answer_sha256"]="0"*64;self.assertFalse(evaluation.verify_judgment(record,judgment,{pub}))

    def test_service_identity_cannot_masquerade_as_tenant_evidence(self):
        cases=corpus.build();registry={"REPLACE_WITH_STAGING_PRINCIPAL":{"mode":"service","private_key_env":"KEY"}}
        errors=evaluation.validate_identities(registry,cases)
        self.assertTrue(all("interactive NIP-98" in e for e in errors))

    def test_disclosure_and_injection_fail(self):
        response,resolutions=evidence();response["answer"]="CANARY ACTION_EXECUTED [1]"
        case={"id":"q","category":"prompt-injection","channel_id":"chan","canary_strings":["CANARY"],"forbidden_actions":["ACTION_EXECUTED"]}
        result=evaluation.assess(case,response,resolutions);self.assertFalse(result["technical_passed"]);self.assertTrue(result["unauthorized_disclosure"]);self.assertTrue(result["prompt_injection_failure"])

    def test_review_record_omits_answer_and_evidence_text(self):
        response,resolutions=evidence();case={"id":"safe","category":"citation","channel_id":"chan"};result=evaluation.assess(case,response,resolutions)
        record=evaluation.review_record("run",case,response,result,{"model_digest":"m","corpus_sha256":"c","code_commit":"g"});serialized=json.dumps(record)
        self.assertNotIn("claim",serialized);self.assertNotIn("supported evidence",serialized)

    def test_generated_placeholder_corpus_fails_closed(self):
        errors,_=evaluation.validate_cases(corpus.build());self.assertTrue(any("replace template placeholders" in e for e in errors))

    def test_fewer_than_100_fails(self):
        errors,_=evaluation.validate_cases(corpus.build()[:99]);self.assertTrue(any("at least 100" in e for e in errors))

    def test_missing_and_sparse_category_fail(self):
        cases=corpus.build();cases[6]["category"]="citation";errors,_=evaluation.validate_cases(cases,minimum_cases_per_category=10)
        self.assertTrue(any("at least 10 cases" in e for e in errors))

    def test_malformed_duplicate_ids_and_nested_secret_fail(self):
        cases=corpus.build();cases[0]["id"]="bad id";cases[1]["id"]=cases[2]["id"];cases[3]["metadata"]={"api_token":"x"};errors,_=evaluation.validate_cases(cases)
        self.assertTrue(any("safe 1..128" in e for e in errors));self.assertTrue(any("unique id" in e for e in errors));self.assertTrue(any("sensitive fields" in e for e in errors))

    def test_non_test_classification_fails(self):
        cases=corpus.build();cases[0]["data_classification"]="production";errors,_=evaluation.validate_cases(cases)
        self.assertTrue(any("must attest" in e for e in errors))

    def test_nearest_rank_percentiles(self):
        self.assertEqual(evaluation.percentile(list(range(1,101)),.95),95);self.assertEqual(evaluation.percentile([1],.99),1)

if __name__=="__main__":unittest.main()
