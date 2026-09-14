"""Exercise staging workload credentials separately from tenant-isolation evidence."""
import argparse,json,os,time,urllib.error,urllib.request
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("matrix");p.add_argument("--url",default="http://127.0.0.1:5011");p.add_argument("--output",required=True);args=p.parse_args();cases=json.loads(Path(args.matrix).read_text());rows=[]
    for case in cases:
        started=time.monotonic();status=0
        try:
            credential=os.environ[case["credential_env"]];body=json.dumps(case.get("payload",{}),separators=(",",":")).encode();request=urllib.request.Request(args.url.rstrip("/")+case["path"],data=body,method=case.get("method","POST"),headers={"Content-Type":"application/json","Authorization":"Bearer "+credential})
            with urllib.request.urlopen(request,timeout=30) as response:status=response.status
        except urllib.error.HTTPError as error:status=error.code
        except Exception as error:rows.append({"id":case["id"],"passed":False,"error":type(error).__name__});continue
        rows.append({"id":case["id"],"status":status,"expected_status":case["expected_status"],"passed":status==case["expected_status"],"seconds":round(time.monotonic()-started,3)})
    report={"evidence_type":"service-allowlist-matrix-not-tenant-isolation","passed":all(r["passed"] for r in rows),"results":rows};Path(args.output).write_text(json.dumps(report,indent=2));print(json.dumps({"cases":len(rows),"passed":report["passed"]}));return 0 if report["passed"] else 1
if __name__=="__main__":raise SystemExit(main())
