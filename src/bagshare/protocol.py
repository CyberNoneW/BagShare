from __future__ import annotations

import base64
import hashlib
from pathlib import Path


PROTOCOL = "bagshare"
PROTOCOL_VERSION = 1
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

