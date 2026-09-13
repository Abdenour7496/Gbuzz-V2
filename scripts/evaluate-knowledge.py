"""Evaluate a curated JSONL question set against the private staging API.

Reports contain identifiers and measurements, never answer text or secrets.
Semantic claim support remains an owner-review input; this gate measures
retrieval, authoritative citation integrity, abstention, and isolation.
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
from urllib.parse import urlsplit


SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_ABSTENTIONS = (
    "no matching knowledge found",
    "insufficient evidence",
    "not enough evidence",
    "cannot answer from the available evidence",
)
CASE_FIELDS = {
    "id", "query", "channel_id", "access_level", "required_document_ids",
    "forbidden_document_ids", "forbidden_strings", "min_recall", "expect_no_answer",
}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_case(case):
    required = {"id", "query", "channel_id"}
    missing = sorted(required - set(case))
    if missing:
        raise ValueError(f"Case is missing required fields: {', '.join(missing)}")
    if not all(isinstance(case[key], str) and case[key].strip() for key in required):
        raise ValueError("Case id, query, and channel_id must be non-empty strings")
    if not SAFE_CASE_ID.fullmatch(case["id"]):
        raise ValueError("Case id must be a bounded non-sensitive identifier")
    unknown = sorted(set(case) - CASE_FIELDS)
    if unknown:
        raise ValueError(f"Case contains unknown fields: {', '.join(unknown)}")
    for field in ("required_document_ids", "forbidden_document_ids", "forbidden_strings"):
        if field in case and not isinstance(case[field], list):
            raise ValueError(f"{field} must be a list")
        if field in case and not all(isinstance(value, str) and value for value in case[field]):
            raise ValueError(f"{field} values must be non-empty strings")
    if not 0 <= float(case.get("min_recall", 1.0)) <= 1:
        raise ValueError("min_recall must be between 0 and 1")
    expects_abstention = case.get("expect_no_answer", False)
    if not isinstance(expects_abstention, bool):
        raise ValueError("expect_no_answer must be a boolean")
    if expects_abstention == bool(case.get("required_document_ids")):
        raise ValueError("Case must define either required_document_ids or expect_no_answer=true")


def validate_target(url, allowed_origins):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError("Evaluation URL must be a plain origin")
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if parsed.path not in ("", "/"):
        raise ValueError("Evaluation URL must not contain a path")
    if parsed.hostname in LOOPBACK_HOSTS:
        if parsed.scheme != "http":
            raise ValueError("Loopback evaluation uses the local HTTP endpoint")
    elif parsed.scheme != "https" or origin not in allowed_origins:
        raise ValueError("Non-loopback evaluation requires HTTPS and an exact --allowed-origin")
    return origin


def document_ids(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"document_id", "source_document_id"} and isinstance(item, str):
                found.add(item)
            found.update(document_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(document_ids(item))
    return found


def authoritative_citation(citation, channel_id):
    ordinal = citation.get("chunk_ordinal")
    return bool(
        citation.get("document_id")
        and isinstance(ordinal, int)
        and ordinal >= 0
        and SHA256.fullmatch(str(citation.get("document_sha256", "")))
        and SHA256.fullmatch(str(citation.get("chunk_sha256", "")))
        and citation.get("channel_id") == channel_id
        and citation.get("lifecycle_state") == "approved"
    )


def assess(case, response):
    citations = response.get("citations", [])
    chunks = response.get("chunks", [])
    found = {chunk.get("document_id") for chunk in chunks}
    cited = {citation.get("document_id") for citation in citations}
    required = set(case.get("required_document_ids", []))
    forbidden = set(case.get("forbidden_document_ids", []))
    refs = [int(number) for number in re.findall(r"\[([0-9]+)\]", response.get("answer", ""))]
    serialized = json.dumps(response, ensure_ascii=False).casefold()

    referenced = set(refs)
    references_valid = (
        all(1 <= number <= len(citations) for number in refs)
        and (not citations or referenced == set(range(1, len(citations) + 1)))
    )
    chunk_anchors = {
        (chunk.get("document_id"), chunk.get("ordinal"), chunk.get("content_sha256"),
         chunk.get("chunk_sha256"), (chunk.get("metadata") or {}).get("channel_id"),
         (chunk.get("metadata") or {}).get("knowledge_state", "approved"))
        for chunk in chunks if isinstance(chunk, dict) and isinstance(chunk.get("metadata") or {}, dict)
    }
    citations_authoritative = bool(citations) and all(
        authoritative_citation(citation, case["channel_id"])
        and (citation.get("document_id"), citation.get("chunk_ordinal"), citation.get("document_sha256"),
             citation.get("chunk_sha256"), citation.get("channel_id"), citation.get("lifecycle_state")) in chunk_anchors
        for citation in citations
    )
    forbidden_content = bool(forbidden & document_ids(response)) or any(
        phrase.casefold() in serialized for phrase in case.get("forbidden_strings", [])
    )
    recall = len(found & required) / len(required) if required else 1.0
    citation_recall = len(cited & required) / len(required) if required else 1.0

    expects_abstention = bool(case.get("expect_no_answer", False))
    answer = str(response.get("answer", "")).strip().casefold()
    abstained = not chunks and not citations and any(answer.startswith(marker) for marker in SAFE_ABSTENTIONS)
    abstention_ok = not expects_abstention or abstained
    evidence_ok = expects_abstention or (
        bool(chunks) and bool(citations) and recall >= case.get("min_recall", 1.0)
        and citation_recall >= case.get("min_recall", 1.0)
    )
    passed = (
        references_valid
        and (expects_abstention or citations_authoritative)
        and not forbidden_content
        and abstention_ok
        and evidence_ok
    )
    return {
        "id": case["id"],
        "recall": recall,
        "citation_recall": citation_recall,
        "references_valid": references_valid,
        "citations_authoritative": citations_authoritative,
        "forbidden_content": forbidden_content,
        "expected_abstention": expects_abstention,
        "abstained": abstained,
        "passed": passed,
    }


def summarize(rows, p95, max_p95_seconds):
    total = len(rows)
    return {
        "cases": total,
        "passed_cases": sum(row["passed"] for row in rows),
        "citation_integrity_rate": sum(
            row.get("references_valid", False) and row.get("citations_authoritative", False)
            for row in rows
        ) / total,
        "forbidden_content_cases": sum(row.get("forbidden_content", False) for row in rows),
        "abstention_pass_rate": (
            sum(row.get("abstained", False) for row in rows if row.get("expected_abstention"))
            / max(1, sum(row.get("expected_abstention", False) for row in rows))
        ),
        "p95_seconds": p95,
        "passed": all(row["passed"] for row in rows) and p95 <= max_p95_seconds,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases")
    parser.add_argument("--url", default="http://127.0.0.1:5001")
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", type=int, default=1, choices=range(1, 17))
    parser.add_argument("--max-p95-seconds", type=float, default=15)
    parser.add_argument("--allowed-origin", action="append", default=[])
    args = parser.parse_args()
    cases = [
        json.loads(line)
        for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases or len(cases) > 10000 or len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("Supply 1..10000 uniquely named cases")
    for case in cases:
        validate_case(case)
    base_url = validate_target(args.url, set(args.allowed_origin))
    secret = os.environ["STACK_API_SECRET"]

    def run(case):
        start = time.monotonic()
        try:
            payload = {key: case[key] for key in ("query", "channel_id", "access_level") if key in case}
            payload.update(top_k=10, max_citations=6)
            request = urllib.request.Request(
                base_url + "/api/ask",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-Gcor-Webhook-Secret": secret},
            )
            with urllib.request.urlopen(request, timeout=120) as result:
                response = json.load(result)
            row = assess(case, response)
        except Exception as error:
            row = {"id": case["id"], "passed": False, "error": type(error).__name__}
        return row | {"seconds": round(time.monotonic() - start, 3)}

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        rows = list(executor.map(run, cases))
    times = sorted(row["seconds"] for row in rows)
    p95 = times[math.ceil(len(times) * 0.95) - 1]
    report = summarize(rows, p95, args.max_p95_seconds) | {
        "concurrency": args.concurrency,
        "results": rows,
    }
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
