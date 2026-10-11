from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

from bagshare.client import download, download_rosbag, get_files, get_manifest
from bagshare.discovery import Device
from bagshare.protocol import decode_file_id, encode_file_id, safe_file, safe_relative_destination, sha256_file
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

    try:
        safe_relative_destination(tmp_path, "../escape.db3")
    except ValueError:
        pass
    else:
        raise AssertionError("manifest path traversal should be rejected")


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


def test_rosbag_directory_manifest_and_resumable_download(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    bag = shared / "highway"
    nested = bag / "segments"
    nested.mkdir(parents=True)
    metadata = b"rosbag2_bagfile_information:\n  version: 5\n"
    first = b"db3-data-" * 100_000
    (bag / "metadata.yaml").write_bytes(metadata)
    (bag / "highway_0.db3").write_bytes(first)
    (nested / "highway_1.mcap").write_bytes(b"")
    ignored = shared / "ordinary-directory"
    ignored.mkdir()
    (ignored / "notes.txt").write_text("not a rosbag")

    server, thread = running_server(shared)
    device = Device("test", "sender", "127.0.0.1", server.server_port)
    try:
        entries = get_files(device)
        assert [entry["name"] for entry in entries] == ["highway"]
        item = entries[0]
        assert item["type"] == "rosbag2"
        assert item["file_count"] == 3

        manifest = get_manifest(device, str(item["id"]))
        assert [entry["path"] for entry in manifest["files"]] == [
            "highway_0.db3",
            "metadata.yaml",
            "segments/highway_1.mcap",
        ]

        destination = tmp_path / "received" / "highway"
        partial = tmp_path / "received" / ".highway.bagshare-part" / "highway_0.db3.part"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(first[:12345])

        result = download_rosbag(device, item, destination, progress=lambda *_: None)
        assert result == destination
        assert (destination / "metadata.yaml").read_bytes() == metadata
        assert (destination / "highway_0.db3").read_bytes() == first
        assert (destination / "segments" / "highway_1.mcap").read_bytes() == b""
        assert not partial.parent.exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
