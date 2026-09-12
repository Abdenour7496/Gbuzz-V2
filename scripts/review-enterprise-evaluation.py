"""Verify owner judgments bound to an exact redacted assurance execution."""
import argparse,importlib.util,json
from pathlib import Path

root=Path(__file__).resolve().parents[1];spec=importlib.util.spec_from_file_location("evaluation",root/"scripts"/"evaluate-knowledge.py");evaluation=importlib.util.module_from_spec(spec);spec.loader.exec_module(evaluation)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("execution_report");parser.add_argument("judgments");parser.add_argument("--reviewers",required=True,help="JSON array of approved reviewer x-only public keys");parser.add_argument("--output",required=True);args=parser.parse_args()
    report=json.loads(Path(args.execution_report).read_text());records={r["id"]:r for r in report.get("review_records",[]) if r.get("category")=="factual-support"};judgments=[json.loads(x) for x in Path(args.judgments).read_text().splitlines() if x.strip()];reviewers=set(json.loads(Path(args.reviewers).read_text()))
    if len(judgments)!=len(records) or len({j.get("id") for j in judgments})!=len(judgments):raise ValueError("exactly one judgment is required for every factual-support record")
    results=[]
    for judgment in judgments:
        record=records.get(judgment.get("id"));valid=bool(record and evaluation.verify_judgment(record,judgment,reviewers));results.append({"id":judgment.get("id"),"valid":valid,"supported":judgment.get("supported") is True if valid else False,"reviewer_pubkey":judgment.get("reviewer_pubkey") if valid else None})
    rate=sum(r["supported"] for r in results)/len(results) if results else 0;threshold=report["slos"]["supported_claim_rate"];passed=report.get("technical_passed",False) and all(r["valid"] for r in results) and rate>=threshold
    final={k:v for k,v in report.items() if k!="review_records"}|{"phase":"complete","supported_claim_rate":rate,"judgment_results":results,"passed":passed}
    Path(args.output).write_text(json.dumps(final,indent=2));print(json.dumps({"phase":"complete","judgments":len(results),"supported_claim_rate":rate,"passed":passed}));return 0 if passed else 1

if __name__=="__main__":raise SystemExit(main())
