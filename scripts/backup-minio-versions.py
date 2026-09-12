"""Export every MinIO object version into a content-addressed backup tree."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import boto3
from botocore.config import Config


def main() -> None:
    output = Path(os.environ.get("BACKUP_OUTPUT", "/backup/minio"))
    blobs = output / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    records: list[dict[str, object]] = []
    for bucket in sorted(item["Name"] for item in client.list_buckets()["Buckets"]):
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket):
            for version in page.get("Versions", []):
                digest = hashlib.sha256()
                response = client.get_object(Bucket=bucket, Key=version["Key"], VersionId=version["VersionId"])
                temporary = blobs / f".{uuid.uuid4().hex}.part"
                try:
                    with temporary.open("wb") as target:
                        while chunk := response["Body"].read(1024 * 1024):
                            digest.update(chunk)
                            target.write(chunk)
                finally:
                    response["Body"].close()
                sha256 = digest.hexdigest()
                final = blobs / sha256
                if final.exists():
                    temporary.unlink()
                else:
                    temporary.replace(final)
                records.append({
                    "bucket": bucket, "key": version["Key"], "version_id": version["VersionId"],
                    "is_latest": bool(version.get("IsLatest")), "size": int(version["Size"]),
                    "etag": str(version.get("ETag", "")).strip('"'), "sha256": sha256,
                    "last_modified": version["LastModified"].isoformat(), "delete_marker": False,
                })
            for marker in page.get("DeleteMarkers", []):
                records.append({
                    "bucket": bucket, "key": marker["Key"], "version_id": marker["VersionId"],
                    "is_latest": bool(marker.get("IsLatest")), "last_modified": marker["LastModified"].isoformat(),
                    "delete_marker": True,
                })
    inventory = {"format": 1, "buckets": sorted({r["bucket"] for r in records}), "versions": records}
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"buckets": len(inventory["buckets"]), "versions": len(records), "blobs": len(list(blobs.iterdir()))}))


if __name__ == "__main__":
    main()
