"""Gate an evaluation against owner review of those exact answer hashes.

Reviews are operator-supplied evidence, not an authentication mechanism. An owner
must retain the reviewed answers and approve the review ledger in their normal
governance process. This command never invents semantic support judgments.
"""
import argparse
import json
from pathlib import Path


def assess(report, reviews, owners, minimum_cases=100, minimum_support=.95):
    results=report.get('results',[])
    ids=[r['id'] for r in results]
    errors=[];total=supported=0
    if len(ids)!=len(set(ids)) or len(ids)<minimum_cases:errors.append('Insufficient distinct evaluated cases')
    if not report.get('passed'):errors.append('Automated evaluation did not pass')
    mapping={r['id']:r for r in reviews}
    if len(mapping)!=len(reviews):errors.append('Duplicate review records')
    for result in results:
        review=mapping.get(result['id'])
        if not review or review.get('reviewer') not in owners or not review.get('reviewed_at'):
            errors.append('Missing owner review: '+result['id']);continue
        if not result.get('response_sha256') or review.get('response_sha256')!=result['response_sha256']:
            errors.append('Review belongs to a different answer: '+result['id']);continue
        n=review.get('factual_claims');s=review.get('supported_claims')
        if type(n) is not int or type(s) is not int or not 0<=s<=n:
            errors.append('Invalid claim counts: '+result['id']);continue
        total+=n;supported+=s
        if review.get('acceptable') is not True:errors.append('Owner rejected case: '+result['id'])
    ratio=supported/total if total else 0
    if ratio<minimum_support:errors.append('Supported factual claim ratio below threshold')
    return {'passed':not errors,'cases':len(ids),'supported_claim_ratio':ratio,'errors':errors}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evaluation');parser.add_argument('reviews');parser.add_argument('--owner',action='append',required=True)
    args=parser.parse_args()
    report=assess(json.loads(Path(args.evaluation).read_text()),json.loads(Path(args.reviews).read_text()),set(args.owner))
    print(json.dumps(report,indent=2));raise SystemExit(0 if report['passed'] else 1)
