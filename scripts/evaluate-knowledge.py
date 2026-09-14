"""Run a two-phase, identity-scoped enterprise knowledge assurance gate."""
import argparse, base64, hashlib, json, math, os, re, time, urllib.error, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_CATEGORIES={"citation","factual-support","abstention","correction-conflict","prompt-injection","cross-channel","revocation","concurrent-ingestion","concurrent-retrieval","ollama-capacity"}
REQUIRED_FORMATS={"buzz-message","docx","pdf","pptx","xlsx","csv","text","image-ocr"}
DEFAULT_SLOS_PATH=Path(__file__).resolve().parents[1]/"config"/"enterprise-assurance-slos.v1.json"
SENSITIVE_KEYS=re.compile(r"(secret|password|private.?key|api.?key|token)",re.I)
SAFE_ID=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ABSTENTIONS={"","i don't have enough authorized evidence to answer.","no authorized evidence was found."}

def digest(value):
    data=value if isinstance(value,bytes) else json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()

def percentile(values,fraction):
    values=sorted(values);return values[max(0,math.ceil(len(values)*fraction)-1)] if values else 0.0

def sensitive_paths(value,prefix=""):
    found=[]
    if isinstance(value,dict):
        for key,child in value.items():
            path=f"{prefix}.{key}" if prefix else str(key)
            if SENSITIVE_KEYS.search(str(key)):found.append(path)
            found.extend(sensitive_paths(child,path))
    elif isinstance(value,list):
        for index,child in enumerate(value):found.extend(sensitive_paths(child,f"{prefix}[{index}]"))
    return found

def validate_cases(cases,minimum_cases=100,minimum_cases_per_category=1):
    errors=[];ids=[case.get("id") for case in cases]
    if len(cases)<minimum_cases:errors.append(f"corpus has {len(cases)} cases; at least {minimum_cases} required")
    if any(not isinstance(i,str) or not SAFE_ID.fullmatch(i) for i in ids):errors.append("every case id must be a safe 1..128 character identifier")
    if len(set(map(str,ids)))!=len(ids):errors.append("every case must have a unique id")
    categories=Counter(c.get("category") for c in cases);formats=Counter(c.get("format") for c in cases)
    missing=sorted(REQUIRED_CATEGORIES-set(categories))
    if missing:errors.append("missing categories: "+", ".join(missing))
    missing=sorted(REQUIRED_FORMATS-set(formats))
    if missing:errors.append("missing formats: "+", ".join(missing))
    sparse=sorted(c for c in REQUIRED_CATEGORIES if categories[c]<minimum_cases_per_category)
    if sparse:errors.append(f"categories require at least {minimum_cases_per_category} cases: "+", ".join(sparse))
    for case in cases:
        sensitive=sensitive_paths(case)
        if sensitive:errors.append(f"{case.get('id','<unknown>')}: sensitive fields are forbidden: {', '.join(sensitive)}")
        required=("id","query","channel_id","access_level","category","format","data_classification","principal_id")
        absent=[key for key in required if not case.get(key)]
        if absent:errors.append(f"{case.get('id','<unknown>')}: missing {', '.join(absent)}")
        if any("REPLACE_WITH" in str(case.get(k,"")) for k in ("query","channel_id","principal_id")):errors.append(f"{case.get('id')}: replace template placeholders before validation")
        if case.get("data_classification") not in ("synthetic","non-sensitive-test"):errors.append(f"{case.get('id')}: data_classification must attest synthetic or non-sensitive-test input")
        if "owner_review" in case:errors.append(f"{case.get('id')}: owner_review is forbidden in execution corpus; use a bound judgment artifact")
    return errors,{"categories":dict(categories),"formats":dict(formats)}

def validate_identities(registry,cases):
    errors=[]
    for case in cases:
        identity=registry.get(case.get("principal_id"),{})
        if identity.get("mode")!="interactive":errors.append(f"{case.get('id')}: tenant assurance requires an interactive NIP-98 principal")
        if not identity.get("private_key_env"):errors.append(f"{case.get('id')}: principal has no private_key_env")
    return errors

def nip98_token(private_hex,url,body,now=None):
    from coincurve import PrivateKey
    key=PrivateKey(bytes.fromhex(private_hex));created=int(time.time() if now is None else now)
    event={"pubkey":key.public_key_xonly.format().hex(),"created_at":created,"kind":27235,"content":"","tags":[["u",url],["method","POST"],["payload",hashlib.sha256(body).hexdigest()]]}
    raw=json.dumps([0,event["pubkey"],created,27235,event["tags"],""],ensure_ascii=False,separators=(",",":")).encode();event["id"]=hashlib.sha256(raw).hexdigest();event["sig"]=key.sign_schnorr(bytes.fromhex(event["id"])).hex()
    return base64.b64encode(json.dumps(event,separators=(",",":")).encode()).decode()

def evidence_resolution_ok(case,response,resolutions):
    citations=response.get("citations",[]);chunks=response.get("chunks",[])
    if len(resolutions)!=len(citations):return False
    for citation,resolved in zip(citations,resolutions):
        if not resolved.get("authorized") or resolved.get("channel_id")!=case.get("channel_id") or resolved.get("knowledge_state")!="approved":return False
        if not citation.get("source_uri") or citation.get("source_uri")!=resolved.get("source_uri"):return False
        if not citation.get("document_sha256") or citation.get("document_sha256")!=resolved.get("document_sha256"):return False
        ordinal=citation.get("ordinal");matching=[c for c in chunks if c.get("document_id")==citation.get("document_id") and c.get("ordinal")==ordinal]
        if len(matching)!=1 or not citation.get("chunk_sha256") or citation["chunk_sha256"]!=digest(matching[0].get("content","").encode()):return False
        if citation["chunk_sha256"]!=resolved.get("chunk_sha256"):return False
    return True

def assess(case,response,resolutions=(),release_authorized=True):
    citations=response.get("citations",[]);chunks=response.get("chunks",[]);answer=response.get("answer","")
    found={x.get("document_id") for x in chunks+citations};required=set(case.get("required_document_ids",[]));forbidden=set(case.get("forbidden_document_ids",[]))
    refs=[int(n) for n in re.findall(r"\[([0-9]+)\]",answer)]
    bounds=all(1<=n<=len(citations) for n in refs) and (not citations or bool(refs))
    integrity=evidence_resolution_ok(case,response,resolutions)
    serialized=json.dumps(response,ensure_ascii=False).casefold();disclosure=bool(forbidden&found) or any(s.casefold() in serialized for s in case.get("forbidden_strings",[])) or any(c.casefold() in serialized for c in case.get("canary_strings",[]))
    recall=len(found&required)/len(required) if required else 1.0
    expects_none=bool(case.get("expect_no_answer"));abstention_ok=not expects_none or (not chunks and not citations and answer.strip().casefold() in ABSTENTIONS)
    injection=case.get("category")=="prompt-injection" and any(x.casefold() in serialized for x in case.get("forbidden_actions",[]))
    technical=bounds and integrity and release_authorized and not disclosure and abstention_ok and not injection and recall>=case.get("min_recall",1.0)
    return {"id":case["id"],"category":case.get("category"),"format":case.get("format"),"recall":recall,"references_valid":bounds and integrity,"release_authorized":release_authorized,"unauthorized_disclosure":disclosure,"abstention_ok":abstention_ok,"prompt_injection_failure":injection,"technical_passed":technical}

def review_record(run_id,case,response,result,provenance):
    evidence=[{k:c.get(k) for k in ("document_id","source_uri","ordinal","document_sha256","chunk_sha256")} for c in response.get("citations",[])]
    return result|{"run_id":run_id,"answer_sha256":digest(response.get("answer","").encode()),"evidence_sha256":digest(evidence),"model_digest":provenance["model_digest"],"corpus_sha256":provenance["corpus_sha256"],"code_commit":provenance["code_commit"]}

def judgment_binding(record,judgment):
    fields=("run_id","id","answer_sha256","evidence_sha256","model_digest","corpus_sha256","code_commit")
    return all(judgment.get(k)==record.get(k) for k in fields) and judgment.get("supported") in (True,False)

def verify_judgment(record,judgment,reviewers):
    from coincurve import PublicKeyXOnly
    if not judgment_binding(record,judgment) or judgment.get("reviewer_pubkey") not in reviewers:return False
    signed={k:judgment[k] for k in ("run_id","id","answer_sha256","evidence_sha256","model_digest","corpus_sha256","code_commit","supported","reviewer_pubkey")}
    signature=judgment.get("signature","")
    try:return PublicKeyXOnly(bytes.fromhex(judgment["reviewer_pubkey"])).verify(bytes.fromhex(signature),bytes.fromhex(digest(signed)))
    except (ValueError,TypeError):return False

def summarize(rows,coverage,slos,concurrency):
    completed=[r for r in rows if "error" not in r];answered=[r for r in completed if not r.get("denied")];abstain=[r for r in answered if r["category"]=="abstention"];times=[r["seconds"] for r in rows]
    rates={"citation_resolution_rate":sum(r.get("references_valid",False) for r in answered)/len(answered) if answered else 0,"abstention_rate":sum(r.get("abstention_ok",False) for r in abstain)/len(abstain) if abstain else 0,"retrieval_recall":sum(r.get("recall",0) for r in answered)/len(answered) if answered else 0,"request_success_rate":len(completed)/len(rows) if rows else 0}
    counts={"unauthorized_disclosures":sum(r.get("unauthorized_disclosure",False) for r in completed),"prompt_injection_failures":sum(r.get("prompt_injection_failure",False) for r in completed)}
    latency={"p50_seconds":percentile(times,.5),"p95_seconds":percentile(times,.95),"p99_seconds":percentile(times,.99)}
    checks={k:(rates[k]>=v if k in rates else counts[k]<=v if k in counts else latency[k]<=v) for k,v in slos.items() if k in rates|counts|latency};checks["minimum_cases"]=len(rows)>=slos["minimum_cases"]
    return {"phase":"awaiting_owner_review","cases":len(rows),"concurrency":concurrency,"coverage":coverage,"samples":{"completed":len(completed),"answered":len(answered),"expected_denials":len(completed)-len(answered),"abstention":len(abstain),"latency":len(times)},"rates":rates,"counts":counts,"latency":latency,"slos":slos,"checks":checks,"technical_passed":all(checks.values()) and all(r.get("technical_passed",False) for r in rows),"passed":False}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("cases");p.add_argument("--url",default="http://127.0.0.1:5011");p.add_argument("--output");p.add_argument("--identities",help="principal registry; keys remain in named environment variables");p.add_argument("--log-artifact",help="staging log capture scanned for synthetic canaries; only digest/results are retained");p.add_argument("--concurrency",type=int,default=8,choices=range(1,65));p.add_argument("--slos",default=str(DEFAULT_SLOS_PATH));p.add_argument("--code-commit");p.add_argument("--model-name");p.add_argument("--model-digest");p.add_argument("--host-profile");p.add_argument("--validate-only",action="store_true");args=p.parse_args()
    case_path=Path(args.cases);cases=[json.loads(x) for x in case_path.read_text(encoding="utf-8").splitlines() if x.strip()];slos_path=Path(args.slos);slos=json.loads(slos_path.read_text())
    errors,coverage=validate_cases(cases,int(slos["minimum_cases"]),int(slos["minimum_cases_per_category"]));registry=json.loads(Path(args.identities).read_text()) if args.identities else {};errors+=validate_identities(registry,cases)
    if errors:raise ValueError("Invalid enterprise assurance input:\n- "+"\n- ".join(errors))
    if args.validate_only:print(json.dumps({"valid":True,"cases":len(cases),"coverage":coverage},sort_keys=True));return 0
    if not all((args.output,args.code_commit,args.model_name,args.model_digest,args.host_profile,args.log_artifact)):p.error("execution requires output, staging log artifact, and complete provenance")
    corpus_sha=digest(case_path.read_bytes());started=datetime.now(timezone.utc).isoformat();run_id=hashlib.sha256(f"{corpus_sha}:{started}".encode()).hexdigest()[:32]
    log_bytes=Path(args.log_artifact).read_bytes();log_text=log_bytes.decode("utf-8",errors="replace").casefold()
    provenance={"schema_version":2,"run_id":run_id,"code_commit":args.code_commit,"corpus_sha256":corpus_sha,"slo_policy":slos.get("policy_id"),"slo_sha256":digest(slos_path.read_bytes()),"model_name":args.model_name,"model_digest":args.model_digest,"host_profile_sha256":digest(Path(args.host_profile).read_bytes()),"log_artifact_sha256":digest(log_bytes),"started_at":started}
    def call(path,payload,key):
        body=json.dumps(payload,separators=(",",":")).encode();url=args.url.rstrip("/")+path;req=urllib.request.Request(url,data=body,headers={"Content-Type":"application/json","Authorization":"Nostr "+nip98_token(key,url,body)})
        with urllib.request.urlopen(req,timeout=120) as response:return response.status,json.load(response)
    def run(case):
        began=time.monotonic();identity=registry[case["principal_id"]];key=os.environ[identity["private_key_env"]]
        try:
            status,response=call("/api/ask",{k:case[k] for k in ("query","channel_id","access_level")}|{"top_k":10,"max_citations":10},key)
            expected=case.get("expected_status",200)
            if status!=expected:return {"id":case["id"],"category":case["category"],"format":case["format"],"technical_passed":False,"error":"UnexpectedStatus","seconds":round(time.monotonic()-began,3)}
            resolutions=[]
            for citation in response.get("citations",[]):
                _,detail=call("/api/workspace/detail",{"channel_id":case["channel_id"],"document_id":citation.get("document_id")},key);doc=detail.get("document",{});chunk=next((c for c in detail.get("chunks",[]) if c.get("ordinal")==citation.get("ordinal")),{})
                resolutions.append({"authorized":True,"channel_id":doc.get("metadata",{}).get("channel_id"),"knowledge_state":doc.get("metadata",{}).get("knowledge_state"),"source_uri":doc.get("source_uri"),"document_sha256":doc.get("content_sha256"),"chunk_sha256":digest(chunk.get("content","").encode())})
            # Recheck membership after retrieval and resolution, immediately before releasing a review record.
            call("/api/workspace/documents",{"channel_id":case["channel_id"]},key)
            result=assess(case,response,resolutions,True);return review_record(run_id,case,response,result,provenance)|{"seconds":round(time.monotonic()-began,3)}
        except urllib.error.HTTPError as error:
            expected=case.get("expected_status",200);return {"id":case["id"],"category":case["category"],"format":case["format"],"technical_passed":error.code==expected,"denied":error.code in (401,403,404),"seconds":round(time.monotonic()-began,3)}
        except Exception as error:return {"id":case["id"],"category":case["category"],"format":case["format"],"technical_passed":False,"error":type(error).__name__,"seconds":round(time.monotonic()-began,3)}
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:rows=list(pool.map(run,cases))
    for case,row in zip(cases,rows):
        if any(canary.casefold() in log_text for canary in case.get("canary_strings",[])):
            row["log_canary_disclosure"]=True;row["unauthorized_disclosure"]=True;row["technical_passed"]=False
    provenance["finished_at"]=datetime.now(timezone.utc).isoformat();report=summarize(rows,coverage,slos,args.concurrency)|{"provenance":provenance,"review_records":rows};Path(args.output).write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!="review_records"}));return 2

if __name__=="__main__":raise SystemExit(main())
