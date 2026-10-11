from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .protocol import BUFFER_SIZE, encode_file_id, safe_file, sha256_file


RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)$")


def list_files(root: Path) -> list[dict[str, object]]:
    files = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_file():
            stat = path.stat()
            files.append(
                {
                    "id": encode_file_id(path.name),
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_ns": stat.st_mtime_ns,
                }
            )
    return files


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
