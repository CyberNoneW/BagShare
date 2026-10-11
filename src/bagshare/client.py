from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

from .discovery import Device
from .protocol import BUFFER_SIZE


def api_url(device: Device, path: str) -> str:
    return f"http://{device.host}:{device.port}{path}"


def get_files(device: Device, timeout: float = 10.0) -> list[dict[str, object]]:
    with urllib.request.urlopen(api_url(device, "/v1/files"), timeout=timeout) as response:
        payload = json.load(response)
    return payload["files"]


def get_remote_hash(device: Device, file_id: str, timeout: float = 3600.0) -> str:
    with urllib.request.urlopen(api_url(device, f"/v1/hash/{file_id}"), timeout=timeout) as response:
        payload = json.load(response)
    return str(payload["sha256"])


def human_size(value: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    raise AssertionError


def default_progress(done: int, total: int, speed: float) -> None:
    ratio = done / total if total else 1.0
    width = 24
    filled = min(width, int(ratio * width))
    eta = (total - done) / speed if speed > 0 else 0
    line = (
        f"\r[{'#' * filled}{'.' * (width - filled)}] {ratio:6.1%}  "
        f"{human_size(done)} / {human_size(total)}  {human_size(speed)}/s  ETA {eta:.0f}s"
    )
    print(line, end="", file=sys.stderr, flush=True)


def download(
    device: Device,
    item: dict[str, object],
    destination: Path,
    progress: Callable[[int, int, float], None] = default_progress,
) -> tuple[Path, bool]:
    total = int(item["size"])
    file_id = str(item["id"])
    part = destination.with_name(destination.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if offset > total:
        raise ValueError(f"partial file is larger than remote file: {part}")

    digest = hashlib.sha256()
    if offset:
        with part.open("rb") as existing:
            while chunk := existing.read(BUFFER_SIZE):
                digest.update(chunk)

    if offset < total:
        request = urllib.request.Request(api_url(device, f"/v1/files/{file_id}"))
        if offset:
            request.add_header("Range", f"bytes={offset}-")
        started = time.monotonic()
        checkpoint_time, checkpoint_bytes = started, offset
        speed = 0.0
        mode = "ab" if offset else "wb"
        with urllib.request.urlopen(request, timeout=60.0) as response, part.open(mode) as target:
            expected_status = 206 if offset else 200
            if response.status != expected_status:
                raise IOError(f"server returned HTTP {response.status}, expected {expected_status}")
            done = offset
            while chunk := response.read(BUFFER_SIZE):
                target.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - checkpoint_time >= 0.2 or done == total:
                    instant = (done - checkpoint_bytes) / max(now - checkpoint_time, 1e-9)
                    speed = instant if speed == 0 else speed * 0.7 + instant * 0.3
                    progress(done, total, speed)
                    checkpoint_time, checkpoint_bytes = now, done
        if done != total:
            raise IOError(f"incomplete download: received {done} of {total} bytes")
    else:
        progress(total, total, 0)
    print(file=sys.stderr)

    remote_hash = get_remote_hash(device, file_id)
    verified = digest.hexdigest() == remote_hash
    if not verified:
        return part, False
    part.replace(destination)
    return destination, True
