"""Safely extract and fully verify a decrypted Gbuzz recovery TAR."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe TAR path: {name}")
    return path.as_posix()


def extract_and_verify(archive: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    with tarfile.open(archive, "r:") as handle:
        members = handle.getmembers()
        total = sum(member.size for member in members)
        if total > archive.stat().st_size:
            raise ValueError("TAR expansion exceeds archive bound")
        for member in members:
            name = _safe_name(member.name)
            if name in seen:
                raise ValueError(f"duplicate TAR path: {name}")
            seen.add(name)
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"unsupported TAR member: {name}")
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.resolve().relative_to(destination.resolve())
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = handle.extractfile(member)
                if source is None:
                    raise ValueError(f"unreadable TAR member: {name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            else:
                raise ValueError(f"unsupported TAR member: {name}")
    manifest_path = destination / "recovery-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("inner recovery manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    declared = {item["path"]: item for item in manifest.get("files", [])}
    if len(declared) != len(manifest.get("files", [])):
        raise ValueError("inner recovery manifest contains duplicate paths")
    actual = {path.relative_to(destination).as_posix(): path for path in destination.rglob("*") if path.is_file() and path != manifest_path}
    if set(actual) != set(declared):
        missing = sorted(set(declared) - set(actual)); extra = sorted(set(actual) - set(declared))
        raise ValueError(f"evidence set mismatch; missing={missing}, unexpected={extra}")
    for name, item in declared.items():
        path = actual[name]
        if path.stat().st_size != int(item["size"]):
            raise ValueError(f"evidence size mismatch: {name}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError(f"evidence hash mismatch: {name}")
    reference = json.loads((destination / "reference-check.json").read_text(encoding="utf-8"))
    if not reference.get("passed") or reference.get("unresolved"):
        raise ValueError("database-to-MinIO reference gate failed")
    return {"passed": True, "files_verified": len(actual), "bytes_verified": sum(p.stat().st_size for p in actual.values()), "delete_markers": reference.get("delete_markers", 0)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--extract-to", type=Path)
    args = parser.parse_args()
    if args.extract_to:
        result = extract_and_verify(args.archive, args.extract_to)
    else:
        with tempfile.TemporaryDirectory(prefix="gbuzz-evidence-") as folder:
            result = extract_and_verify(args.archive, Path(folder))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
