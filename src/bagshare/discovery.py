from __future__ import annotations

import json
import socket
import struct
import threading
import time
from dataclasses import dataclass

from .protocol import DISCOVERY_PORT, MULTICAST_GROUP, PROTOCOL, PROTOCOL_VERSION


@dataclass(frozen=True)
class Device:
    device_id: str
    name: str
    host: str
    port: int


class Announcer:
    def __init__(self, device_id: str, name: str, port: int, interval: float = 1.0):
        self.message = json.dumps(
            {
                "protocol": PROTOCOL,
                "version": PROTOCOL_VERSION,
                "id": device_id,
                "name": name,
                "port": port,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="bagshare-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            while not self._stop.is_set():
                try:
                    sock.sendto(self.message, (MULTICAST_GROUP, DISCOVERY_PORT))
                except OSError:
                    pass
                self._stop.wait(self.interval)


def discover(timeout: float = 3.0) -> list[Device]:
    devices: dict[str, Device] = {}
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.bind(("", DISCOVERY_PORT))
        membership = struct.pack("=4sl", socket.inet_aton(MULTICAST_GROUP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                payload, address = sock.recvfrom(4096)
            except socket.timeout:
                break
            try:
                item = json.loads(payload)
                if item.get("protocol") != PROTOCOL or item.get("version") != PROTOCOL_VERSION:
                    continue
                device = Device(
                    device_id=str(item["id"]),
                    name=str(item["name"]),
                    host=address[0],
                    port=int(item["port"]),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            devices[device.device_id] = device
    return sorted(devices.values(), key=lambda device: (device.name.casefold(), device.device_id))

