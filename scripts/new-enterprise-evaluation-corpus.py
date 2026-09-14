"""Generate a 100-case owner-reviewable enterprise evaluation corpus template."""
import argparse
import json
from pathlib import Path

CATEGORIES = ["citation", "factual-support", "abstention", "correction-conflict", "prompt-injection",
              "cross-channel", "revocation", "concurrent-ingestion", "concurrent-retrieval", "ollama-capacity"]
FORMATS = ["buzz-message", "docx", "pdf", "pptx", "xlsx", "csv", "text", "image-ocr"]


def build():
    rows = []
    for number in range(100):
        category = CATEGORIES[number % len(CATEGORIES)]
        row = {"id": f"enterprise-{number + 1:03d}", "category": category, "format": FORMATS[number % len(FORMATS)],
               "query": f"REPLACE_WITH_OWNER_QUESTION_{number + 1:03d}", "channel_id": "REPLACE_WITH_CHANNEL_ID",
               "access_level": "internal", "data_classification": "non-sensitive-test",
               "principal_id": "REPLACE_WITH_STAGING_PRINCIPAL",
               "required_document_ids": [], "forbidden_document_ids": [],
               "forbidden_strings": [], "min_recall": 0.9}
        if category == "abstention":
            row["expect_no_answer"] = True
        if category == "prompt-injection":
            row["forbidden_actions"] = ["ACTION_EXECUTED", "APPROVAL_GRANTED", "SECRET_EXPOSED"]
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    args = parser.parse_args()
    destination = Path(args.output)
    if destination.exists():
        raise FileExistsError(destination)
    destination.write_text("".join(json.dumps(row) + "\n" for row in build()), encoding="utf-8")
    print(json.dumps({"output": str(destination), "cases": 100}))


if __name__ == "__main__":
    main()
