from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path, PurePosixPath


PROTOCOL = "bagshare"
PROTOCOL_VERSION = 2
MULTICAST_GROUP = "239.255.42.99"
DISCOVERY_PORT = 45892
BUFFER_SIZE = 1024 * 1024


def encode_file_id(relative_path: str) -> str:
    return base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")


def decode_file_id(file_id: str) -> str:
    padding = "=" * (-len(file_id) % 4)
    return base64.urlsafe_b64decode(file_id + padding).decode("utf-8")


def safe_file(root: Path, file_id: str) -> Path:
    try:
        candidate = (root / decode_file_id(file_id)).resolve()
    except (ValueError, UnicodeError) as exc:
        raise FileNotFoundError(file_id) from exc
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise FileNotFoundError(file_id) from exc
    if not candidate.is_file():
        raise FileNotFoundError(file_id)
    return candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(BUFFER_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_sha256(files: list[dict[str, object]]) -> str:
    """Hash a stable representation of a directory snapshot."""
    snapshot = [
        {
            "path": str(item["path"]),
            "size": int(item["size"]),
            "modified_ns": int(item["modified_ns"]),
        }
        for item in files
    ]
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_relative_destination(root: Path, relative_path: str) -> Path:
    """Resolve a manifest path below root using platform-independent rules."""
    posix_path = PurePosixPath(relative_path)
    if (
        not relative_path
        or posix_path.is_absolute()
        or any(part in ("", ".", "..") for part in posix_path.parts)
    ):
        raise ValueError(f"unsafe manifest path: {relative_path!r}")
    candidate = (root / Path(*posix_path.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"unsafe manifest path: {relative_path!r}") from exc
    return candidate
