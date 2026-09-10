"""Evaluate a curated JSONL question set against the private staging API.

No answer text or secrets are written to the report. Semantic claim support still
requires owner review; these checks measure retrieval and reference integrity.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.request


def assess(case, response):
    citations=response.get('citations',[])
    found={c.get('document_id') for c in response.get('chunks',[])}
    required=set(case.get('required_document_ids',[]))
    forbidden=set(case.get('forbidden_document_ids',[]))
    refs=[int(n) for n in re.findall(r'\[([0-9]+)\]',response.get('answer',''))]
    serialized=json.dumps(response,ensure_ascii=False).casefold()
    reference_ok=all(1<=n<=len(citations) for n in refs) and (not citations or bool(refs))
    violations=bool(forbidden & (found|{c.get('document_id') for c in citations})) or any(
        phrase.casefold() in serialized for phrase in case.get('forbidden_strings',[]))
    recall=len(found & required)/len(required) if required else 1.0
    no_answer_ok=not case.get('expect_no_answer',False) or not response.get('chunks')
    return {'id':case['id'],'recall':recall,'references_valid':reference_ok,'forbidden_content':violations,
            'passed':reference_ok and not violations and no_answer_ok and recall>=case.get('min_recall',1.0)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases');parser.add_argument('--url',default='http://127.0.0.1:5001')
    parser.add_argument('--output',required=True);parser.add_argument('--concurrency',type=int,default=1,choices=range(1,17))
    parser.add_argument('--max-p95-seconds',type=float,default=15)
    args=parser.parse_args()
    cases=[json.loads(line) for line in Path(args.cases).read_text(encoding='utf-8').splitlines() if line.strip()]
    if not cases or len(cases)>10000 or len({c['id'] for c in cases})!=len(cases):raise ValueError('Supply 1..10000 uniquely named cases')
    secret=os.environ['STACK_API_SECRET']
    def run(case):
        start=time.monotonic()
        try:
            payload={k:case[k] for k in ('query','channel_id','access_level') if k in case}
            payload.update(top_k=10,max_citations=6)
            request=urllib.request.Request(args.url.rstrip('/')+'/api/ask',data=json.dumps(payload).encode(),
                headers={'Content-Type':'application/json','X-Gcor-Webhook-Secret':secret})
            with urllib.request.urlopen(request,timeout=120) as result:response=json.load(result)
            row=assess(case,response)
        except Exception as error:row={'id':case['id'],'passed':False,'error':type(error).__name__}
        return row|{'seconds':round(time.monotonic()-start,3)}
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor: rows=list(executor.map(run,cases))
    times=sorted(r['seconds'] for r in rows);p95=times[math.ceil(len(times)*.95)-1]
    report={'cases':len(rows),'passed':all(r['passed'] for r in rows) and p95<=args.max_p95_seconds,
            'p95_seconds':p95,'concurrency':args.concurrency,'results':rows}
    Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
