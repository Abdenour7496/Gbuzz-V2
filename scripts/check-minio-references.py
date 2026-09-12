"""Fail unless each database-referenced MinIO object exists in the exported version inventory."""
import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

HEX_64 = re.compile(r"^[0-9a-fA-F]{64}$")


def expand_reference(row):
    if row.get("source") != "buzz_event_imeta":
        return [row]
    references = []
    tags = row.get("tags")
    if not isinstance(tags, list):
        return references
    for index, tag in enumerate(tags):
        if not isinstance(tag, list) or not tag or tag[0] != "imeta":
            continue
        fields = {}
        for part in tag[1:]:
            if isinstance(part, str) and " " in part:
                name, value = part.split(" ", 1)
                fields[name] = value
        parsed = urlparse(fields.get("url", ""))
        if not parsed.path.startswith("/media/"):
            continue
        key = Path(parsed.path).name
        expected = fields.get("x") or key.split(".", 1)[0]
        if not key or not HEX_64.fullmatch(expected):
            references.append({"source":"events.tags","id":row.get("id"),"tag_index":index,
                               "bucket":row.get("bucket","buzz-media"),"key":key,
                               "invalid_reason":"malformed media attachment digest"})
            continue
        references.append({"source":"events.tags","id":row.get("id"),"tag_index":index,
                           "bucket":row.get("bucket","buzz-media"),"key":key,"sha256":expected.lower()})
    return references


def main():
    parser=argparse.ArgumentParser();parser.add_argument("references",type=Path);parser.add_argument("inventory",type=Path);parser.add_argument("output",type=Path);args=parser.parse_args()
    inventory=json.loads(args.inventory.read_text(encoding="utf-8"));versions=inventory["versions"]
    current={(item["bucket"],item["key"]):item for item in versions if item.get("is_latest") and not item.get("delete_marker")}
    unresolved=[];count=0
    for line in args.references.read_text(encoding="utf-8").splitlines():
        if not line.strip():continue
        for row in expand_reference(json.loads(line)):
            count+=1;item=current.get((row["bucket"],row["key"]))
            if row.get("invalid_reason") or item is None or (row.get("sha256") and item.get("sha256","").lower()!=row["sha256"]):unresolved.append(row)
    report={"passed":not unresolved,"references_checked":count,"unresolved":unresolved,"delete_markers":sum(1 for item in versions if item.get("delete_marker"))}
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True),encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"references_checked":count,"unresolved_count":len(unresolved)}))
    if unresolved:raise SystemExit(1)

if __name__=="__main__":main()
