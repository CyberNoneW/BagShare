from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .protocol import BUFFER_SIZE, decode_file_id, encode_file_id, manifest_sha256, safe_file, sha256_file


RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)$")
ROSBAG_DATA_SUFFIXES = {".db3", ".mcap"}


def rosbag_files(root: Path, bag: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    paths = sorted(bag.rglob("*"), key=lambda item: item.relative_to(bag).as_posix().casefold())
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        stat = path.stat()
        files.append(
            {
                "id": encode_file_id(path.relative_to(root).as_posix()),
                "path": path.relative_to(bag).as_posix(),
                "size": stat.st_size,
                "modified_ns": stat.st_mtime_ns,
            }
        )
    return files


def is_rosbag_directory(path: Path) -> bool:
    metadata = path / "metadata.yaml"
    if not metadata.is_file() or metadata.is_symlink():
        return False
    return any(
        item.is_file() and not item.is_symlink() and item.suffix.lower() in ROSBAG_DATA_SUFFIXES
        for item in path.rglob("*")
    )


def build_manifest(root: Path, bag_id: str) -> dict[str, object]:
    try:
        bag = (root / decode_file_id(bag_id)).resolve()
        bag.relative_to(root.resolve())
    except (ValueError, UnicodeError) as exc:
        raise FileNotFoundError(bag_id) from exc
    if not bag.is_dir() or bag.is_symlink() or not is_rosbag_directory(bag):
        raise FileNotFoundError(bag_id)
    files = rosbag_files(root, bag)
    return {
        "id": bag_id,
        "name": bag.name,
        "type": "rosbag2",
        "size": sum(int(item["size"]) for item in files),
        "file_count": len(files),
        "files": files,
        "manifest_sha256": manifest_sha256(files),
    }


def list_files(root: Path) -> list[dict[str, object]]:
    entries = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            entries.append(
                {
                    "id": encode_file_id(path.name),
                    "name": path.name,
                    "type": "file",
                    "size": stat.st_size,
                    "modified_ns": stat.st_mtime_ns,
                }
            )
        elif path.is_dir() and not path.is_symlink() and is_rosbag_directory(path):
            files = rosbag_files(root, path)
            entries.append(
                {
                    "id": encode_file_id(path.name),
                    "name": path.name,
                    "type": "rosbag2",
                    "size": sum(int(item["size"]) for item in files),
                    "file_count": len(files),
                }
            )
    return entries


class BagShareServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], root: Path):
        self.root = root.resolve()
        super().__init__(address, BagShareHandler)


class BagShareHandler(BaseHTTPRequestHandler):
    server: BagShareServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/v1/files":
            self._json({"files": list_files(self.server.root)})
            return
        if path.startswith("/v1/manifests/"):
            self._manifest(unquote(path.removeprefix("/v1/manifests/")))
            return
        if path.startswith("/v1/hash/"):
            self._hash(unquote(path.removeprefix("/v1/hash/")))
            return
        if path.startswith("/v1/files/"):
            self._file(unquote(path.removeprefix("/v1/files/")), send_body=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        path = urlsplit(self.path).path
        if path.startswith("/v1/files/"):
            self._file(unquote(path.removeprefix("/v1/files/")), send_body=False)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _resolve(self, file_id: str) -> Path | None:
        try:
            return safe_file(self.server.root, file_id)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return None

    def _hash(self, file_id: str) -> None:
        path = self._resolve(file_id)
        if path is not None:
            self._json({"sha256": sha256_file(path)})

    def _manifest(self, bag_id: str) -> None:
        try:
            manifest = build_manifest(self.server.root, bag_id)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._json(manifest)

    def _file(self, file_id: str, send_body: bool) -> None:
        path = self._resolve(file_id)
        if path is None:
            return
        size = path.stat().st_size
        start, end = 0, max(0, size - 1)
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            match = RANGE_RE.fullmatch(range_header.strip())
            if not match:
                self._range_error(size)
                return
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
            if start >= size or end < start or end >= size:
                self._range_error(size)
                return
            status = HTTPStatus.PARTIAL_CONTENT

        content_length = 0 if size == 0 else end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(content_length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body or content_length == 0:
            return
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = content_length
                while remaining:
                    chunk = source.read(min(BUFFER_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _range_error(self, size: int) -> None:
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()


def create_server(root: Path, host: str = "0.0.0.0", port: int = 0) -> BagShareServer:
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    return BagShareServer((host, port), root)
