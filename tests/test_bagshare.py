from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

from bagshare.client import download, get_files
from bagshare.discovery import Device
from bagshare.protocol import decode_file_id, encode_file_id, safe_file, sha256_file
from bagshare.server import create_server


def running_server(root: Path):
    server = create_server(root, host="127.0.0.1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_file_id_round_trip_and_path_safety(tmp_path: Path) -> None:
    path = tmp_path / "测试 bag.mcap"
    path.write_bytes(b"data")
    file_id = encode_file_id(path.name)
    assert decode_file_id(file_id) == path.name
    assert safe_file(tmp_path, file_id) == path

    outside = tmp_path.parent / "outside.mcap"
    outside.write_bytes(b"nope")
    try:
        safe_file(tmp_path, encode_file_id("../outside.mcap"))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("path traversal should be rejected")


def test_list_range_and_download_resume(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    content = (b"0123456789abcdef" * 100_000) + b"end"
    source = shared / "large.mcap"
    source.write_bytes(content)
    server, thread = running_server(shared)
    device = Device("test", "sender", "127.0.0.1", server.server_port)
    try:
        files = get_files(device)
        assert [item["name"] for item in files] == ["large.mcap"]

        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/v1/files/{files[0]['id']}",
            headers={"Range": "bytes=10-19"},
        )
        with urllib.request.urlopen(request) as response:
            assert response.status == 206
            assert response.headers["Content-Range"] == f"bytes 10-19/{len(content)}"
            assert response.read() == content[10:20]

        destination = tmp_path / "received.mcap"
        part = tmp_path / "received.mcap.part"
        part.write_bytes(content[:12345])
        result, verified = download(device, files[0], destination, progress=lambda *_: None)
        assert verified
        assert result == destination
        assert destination.read_bytes() == content
        assert sha256_file(destination) == sha256_file(source)
        assert not part.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
