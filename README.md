# BagShare

BagShare is a small, dependency-free CLI for moving large ROS bag files between
Linux, Windows, and macOS machines on the same offline LAN. It discovers senders
with UDP multicast and transfers files over HTTP/TCP with resume support and
SHA-256 verification.

## Install for development

Python 3.9 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
python -m pip install -e .
```

## Use

On the sending computer:

```bash
bagshare serve ~/rosbags
```

On the receiving computer:

```bash
bagshare discover
bagshare list buzz-linux
bagshare pull buzz-linux:2
```

Use `-o PATH` to choose the output path. An interrupted transfer remains as a
`PATH.part` file; running the same pull command again resumes from that file.

The sender shares regular files directly inside the selected directory. Folder
transfer is intentionally deferred to a later version.

## Network behavior

- Discovery uses IPv4 multicast group `239.255.42.99`, UDP port `45892`, TTL 1.
- The HTTP server binds to all IPv4 interfaces and selects an ephemeral port by
  default. Use `--port` to choose a fixed port.
- BagShare never changes interface addresses. For direct Ethernet connections,
  the operating systems must establish compatible link-local addresses.
- Host firewalls must allow Python/BagShare to receive the discovery UDP packet
  and incoming TCP connection.

This first version has no authentication or encryption. Use it only on a trusted
local network.

## Test

```bash
python -m pytest
```
