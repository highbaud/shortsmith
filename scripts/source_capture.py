"""Fetch and capture public sources without reaching private networks or user sessions.

The HTTP deadline matches gen_broll's 20-second timeout. Chromium navigation uses
the bundled Puppeteer's 30-second default; the outer timeout covers both phases.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

HTTP_TIMEOUT = 20
BROWSER_TIMEOUT = 30
# At most a 2x-density 1080x1920 source, including 3840x2160 landscape images.
MAX_IMAGE_PIXELS = (1080 * 2) * (1920 * 2)
# One uncompressed 16-bit RGBA source frame is the HTTP body budget.
MAX_BODY_BYTES = MAX_IMAGE_PIXELS * 4 * 2
_ROOT = Path(__file__).resolve().parent.parent
_UA = "ShortSmith public-source capture"
_REDIRECTS = {301, 302, 303, 307, 308}


def _require_online() -> None:
    if os.environ.get("SHORTSMITH_BROLL_OFFLINE") == "1":
        raise RuntimeError("Public source capture is disabled in offline mode")


def _public_url(url: str) -> tuple[urllib.parse.SplitResult, str, int, str]:
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise ValueError("Source URL contains whitespace or control characters")
    if "\\" in url:
        raise ValueError("Source URL contains a backslash")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Source URL must use public HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Source URL must not contain user credentials")
    host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Source URL host is not public")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        _check_public_address(literal)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed, host, port, urllib.parse.urlunsplit(parsed._replace(fragment=""))


def _check_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    checked = getattr(ip, "ipv4_mapped", None) or ip
    if not checked.is_global or checked.is_multicast or checked.is_reserved:
        raise ValueError("Source host resolves to a non-public address")


def _public_addresses(host: str, port: int) -> list[str]:
    answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not answers:
        raise ValueError("Source host has no public address")
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    for address in addresses:
        _check_public_address(ipaddress.ip_address(address))
    return addresses


def _read_response(response: http.client.HTTPResponse, deadline: float,
                   connection_socket: socket.socket | None) -> bytes:
    encoding = response.getheader("Content-Encoding", "identity").strip().lower()
    if encoding != "identity":
        raise ValueError("Public source uses an unexpected content encoding")
    declared = response.getheader("Content-Length")
    if declared and int(declared) > MAX_BODY_BYTES:
        raise ValueError("Public source body exceeds the byte limit")
    chunks: list[bytes] = []
    size = 0
    # TLS plaintext records contain at most 2**14 bytes (RFC 8446, section 5.1).
    while time.monotonic() < deadline:
        if connection_socket is not None:
            connection_socket.settimeout(deadline - time.monotonic())
        chunk = response.read1(min(2**14, MAX_BODY_BYTES - size + 1))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ValueError("Public source body exceeds the byte limit")
        chunks.append(chunk)
    raise TimeoutError("Public source download exceeded the HTTP deadline")


def _request_public(url: str, deadline: float) -> tuple[bytes, str, str | None]:
    parsed, host, port, _ = _public_url(url)
    address = _public_addresses(host, port)[0]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Public source download exceeded the HTTP deadline")
    connection_type = (http.client.HTTPSConnection if parsed.scheme == "https"
                       else http.client.HTTPConnection)
    connection = connection_type(host, port, timeout=remaining)
    # Pin the checked address. HTTPSConnection still validates TLS for `host`.
    def connect_checked(*args: object, **kwargs: object) -> socket.socket:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Public source download exceeded the HTTP deadline")
        return socket.create_connection((address, port), timeout=remaining)

    connection._create_connection = connect_checked
    target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    try:
        connection.request("GET", target, headers={"User-Agent": _UA, "Accept-Encoding": "identity"})
        connection_socket = connection.sock
        response = connection.getresponse()
        if response.status in _REDIRECTS:
            location = response.getheader("Location")
            if not location:
                raise ValueError("Public source redirect has no Location")
            return b"", "", urllib.parse.urljoin(url, location)
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Public source returned HTTP {response.status}")
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        return _read_response(response, deadline, connection_socket), content_type, None
    finally:
        connection.close()


def fetch_public(url: str) -> tuple[bytes, str, str]:
    """Fetch public bytes; check and pin DNS on each redirect. Never use proxies."""
    _require_online()
    deadline = time.monotonic() + HTTP_TIMEOUT
    _, _, _, current = _public_url(url)
    seen: set[str] = set()
    # Match urllib's documented/default redirect allowance, without using its
    # automatic redirects (which would skip the public-address check).
    while len(seen) <= urllib.request.HTTPRedirectHandler.max_redirections:
        if current in seen:
            raise ValueError("Public source has a redirect loop")
        seen.add(current)
        data, content_type, redirect = _request_public(current, deadline)
        if redirect is None:
            return data, content_type, current
        _, _, _, current = _public_url(redirect)
    raise ValueError("Public source exceeded the standard HTTP redirect limit")


def _raster_dimensions(data: bytes) -> tuple[int, int]:
    """Read raster dimensions without allocating decoded pixels."""
    try:
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
            return struct.unpack_from(">II", data, 16)
        if data.startswith((b"GIF87a", b"GIF89a")):
            return struct.unpack_from("<HH", data, 6)
        if data.startswith(b"BM"):
            dib_size = struct.unpack_from("<I", data, 14)[0]
            if dib_size == 12:
                return struct.unpack_from("<HH", data, 18)
            if dib_size >= 40:
                width, height = struct.unpack_from("<ii", data, 18)
                return width, abs(height)
        if data.startswith(b"\xff\xd8\xff"):
            offset = 2
            while offset < len(data):
                if data[offset] != 0xFF:
                    break
                while offset < len(data) and data[offset] == 0xFF:
                    offset += 1
                marker = data[offset]
                offset += 1
                if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
                    continue
                if marker in {0xD9, 0xDA}:
                    break
                length = struct.unpack_from(">H", data, offset)[0]
                if length < 2 or offset + length > len(data):
                    break
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    height, width = struct.unpack_from(">HH", data, offset + 3)
                    return width, height
                offset += length
        if data.startswith((b"II*\x00", b"MM\x00*")):
            endian = "<" if data[:2] == b"II" else ">"
            offset = struct.unpack_from(endian + "I", data, 4)[0]
            count = struct.unpack_from(endian + "H", data, offset)[0]
            dimensions = {}
            for index in range(count):
                entry = offset + 2 + index * 12
                tag, kind, size = struct.unpack_from(endian + "HHI", data, entry)
                if tag in {256, 257} and size == 1 and kind in {3, 4}:
                    if tag in dimensions:
                        raise ValueError("Source image has duplicate raster dimensions")
                    dimensions[tag] = struct.unpack_from(
                        endian + ("H" if kind == 3 else "I"), data, entry + 8,
                    )[0]
            if len(dimensions) == 2:
                return dimensions[256], dimensions[257]
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            kind = data[12:16]
            if kind == b"VP8X" and len(data) >= 30:
                return (1 + int.from_bytes(data[24:27], "little"),
                        1 + int.from_bytes(data[27:30], "little"))
            if kind == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
                width, height = struct.unpack_from("<HH", data, 26)
                return width & 0x3FFF, height & 0x3FFF
            if kind == b"VP8L" and data[20:21] == b"\x2f":
                bits = struct.unpack_from("<I", data, 21)[0]
                return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    except (IndexError, struct.error):
        pass
    raise ValueError("Source image has invalid or unsupported raster dimensions")


def _decode_raster(data: bytes) -> object:
    import cv2
    import numpy as np

    signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a",
                  b"BM", b"II*\x00", b"MM\x00*")
    is_webp = data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if not data.startswith(signatures) and not is_webp:
        raise ValueError("Source image is not a supported raster image")
    if data.startswith(signatures[0]) and (len(data) < 57 or b"IEND" not in data):
        # 57 bytes is PNG's signature plus required IHDR, IDAT, and IEND overhead.
        raise ValueError("Source PNG is truncated")
    width, height = _raster_dimensions(data)
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Source image exceeds the pixel limit")
    try:
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    except cv2.error as error:
        raise ValueError("Source image cannot be decoded") from error
    if image is None or image.size == 0:
        raise ValueError("Source image cannot be decoded")
    return image


def _write_atomic(dest: Path, data: bytes) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, suffix=dest.suffix, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)
    return dest


def download_image(url: str, dest: Path) -> Path:
    """Save decoded pixels as PNG, removing source metadata without text edits."""
    import cv2

    data, content_type, _ = fetch_public(url)
    if content_type in {"text/html", "application/xhtml+xml", "image/svg+xml"}:
        raise ValueError("Source image response is HTML or SVG")
    image = _decode_raster(data)
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise ValueError("Source image cannot be saved as PNG")
    return _write_atomic(Path(dest).with_suffix(".png"), encoded.tobytes())


def capture_page(url: str, dest: Path, *, selector: str | None = None, quote: str = "") -> Path:
    """Save an original page screenshot; an exact quote can guide the DOM crop."""
    _require_online()
    _public_url(url)
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Page capture needs the existing Node.js runtime")
    dest = Path(dest).with_suffix(".png")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shortsmith-source-") as directory:
        screenshot = Path(directory) / "original.png"
        payload = json.dumps({"url": url, "dest": str(screenshot), "selector": selector,
                              "quote": quote, "httpTimeout": HTTP_TIMEOUT * 1000,
                              "browserTimeout": BROWSER_TIMEOUT * 1000})
        try:
            result = subprocess.run(
                [node, str(Path(__file__).with_name("capture_source.mjs"))],
                input=payload, capture_output=True, text=True, encoding="utf-8",
                cwd=_ROOT / "remotion", timeout=HTTP_TIMEOUT + BROWSER_TIMEOUT * 2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"Public page capture failed: {error}") from error
        if result.returncode or not screenshot.is_file():
            raise RuntimeError(f"Public page capture failed: {result.stderr.strip() or 'no screenshot'}")
        data = screenshot.read_bytes()
        _decode_raster(data)
        return _write_atomic(dest, data)
