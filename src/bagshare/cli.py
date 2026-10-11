from __future__ import annotations

import argparse
import socket
import sys
import threading
import uuid
from pathlib import Path

from . import __version__
from .client import download, download_rosbag, get_files, human_size
from .discovery import Announcer, Device, discover
from .server import create_server


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="bagshare", description="Transfer large ROS bag files over a local network")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = result.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="share files from a directory")
    serve.add_argument("directory", type=Path)
    serve.add_argument("--name", default=socket.gethostname(), help="device name shown to receivers")
    serve.add_argument("--port", type=int, default=0, help="HTTP port (default: automatic)")

    discovery = sub.add_parser("discover", help="find BagShare senders")
    discovery.add_argument("--timeout", type=float, default=3.0)

    listing = sub.add_parser("list", help="list files shared by a sender")
    listing.add_argument("device")
    listing.add_argument("--timeout", type=float, default=3.0)

    pull = sub.add_parser("pull", help="download a file or ROS 2 bag, e.g. buzz-linux:2")
    pull.add_argument("selector")
    pull.add_argument("--output", "-o", type=Path)
    pull.add_argument("--timeout", type=float, default=3.0)
    return result


def choose_device(name: str, timeout: float) -> Device:
    devices = discover(timeout)
    exact = [item for item in devices if item.name == name or item.device_id == name]
    if not exact:
        raise RuntimeError(f"device not found: {name}")
    if len(exact) > 1:
        raise RuntimeError(f"multiple devices are named {name!r}; use the device id shown by discover")
    return exact[0]


def print_files(files: list[dict[str, object]]) -> None:
    if not files:
        print("No files shared.")
        return
    width = len(str(len(files)))
    for index, item in enumerate(files, 1):
        if item.get("type") == "rosbag2":
            kind = f"ROS 2 bag, {int(item['file_count'])} files"
        else:
            kind = "file"
        print(f"{index:>{width}}  {str(item['name']):<40} {human_size(int(item['size'])):>10}  {kind}")


def serve_command(args: argparse.Namespace) -> int:
    server = create_server(args.directory, port=args.port)
    device_id = uuid.uuid4().hex[:12]
    announcer = Announcer(device_id, args.name, server.server_port)
    thread = threading.Thread(target=server.serve_forever, name="bagshare-http", daemon=True)
    thread.start()
    announcer.start()
    print("BagShare running")
    print(f"Device: {args.name}")
    print(f"ID: {device_id}")
    print(f"Port: {server.server_port}")
    print(f"Directory: {server.root}")
    print("Waiting for receivers... (Ctrl-C to stop)")
    try:
        while thread.is_alive():
            thread.join(timeout=1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        announcer.stop()
        server.shutdown()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            code = serve_command(args)
        elif args.command == "discover":
            devices = discover(args.timeout)
            if not devices:
                print("No BagShare devices found.")
            for index, item in enumerate(devices, 1):
                print(f"{index}. {item.name}  {item.host}:{item.port}  id={item.device_id}")
            code = 0
        elif args.command == "list":
            print_files(get_files(choose_device(args.device, args.timeout)))
            code = 0
        elif args.command == "pull":
            try:
                device_name, number_text = args.selector.rsplit(":", 1)
                number = int(number_text)
            except (ValueError, AttributeError) as exc:
                raise ValueError("selector must look like DEVICE:NUMBER") from exc
            device = choose_device(device_name, args.timeout)
            files = get_files(device)
            if number < 1 or number > len(files):
                raise ValueError(f"file number must be between 1 and {len(files)}")
            item = files[number - 1]
            destination = args.output or Path(str(item["name"]))
            if destination.exists():
                raise FileExistsError(f"destination already exists: {destination}")
            print(str(item["name"]))
            if item.get("type") == "rosbag2":
                path = download_rosbag(device, item, destination)
                print(f"Saved ROS 2 bag to {path} (all files verified)")
            else:
                path, verified = download(device, item, destination)
                if not verified:
                    raise IOError(f"SHA-256 verification failed; partial file kept at {path}")
                print(f"Saved to {path} (SHA-256 verified)")
            code = 0
        else:
            raise AssertionError(args.command)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"bagshare: error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
