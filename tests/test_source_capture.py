"""Public source capture boundaries, with no live network or browser session."""
from __future__ import annotations

import io
import json
import shutil
import socket
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import source_capture  # noqa: E402


def _dns(address: str = "93.184.216.34") -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


def _response(data: bytes = b"public article", content_type: str = "text/html",
              status: int = 200, location: str = "") -> MagicMock:
    response = MagicMock(status=status)
    response.read1.side_effect = io.BytesIO(data).read1
    headers = {"Content-Type": content_type, "Location": location}
    response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
    return response


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"ai_generated\x00fixture-metadata")
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\x00\x00\xff")) + chunk(b"IEND", b""))


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://localhost/a", "http://127.0.0.1/",
    "http://[::1]/", "http://169.254.169.254/", "http://10.0.0.1/",
    "https://user:pass@example.com/", "https://example.com/\nheader",
])
def test_fetch_rejects_unsafe_urls_without_http(url: str) -> None:
    with patch("http.client.HTTPConnection.connect") as connect:
        with pytest.raises(ValueError):
            source_capture.fetch_public(url)
    connect.assert_not_called()


def test_fetch_checks_all_dns_answers() -> None:
    with patch("socket.getaddrinfo", return_value=_dns() + _dns("10.0.0.1")), \
            patch("http.client.HTTPSConnection") as connection:
        with pytest.raises(ValueError, match="non-public"):
            source_capture.fetch_public("https://example.org/")
    connection.assert_not_called()


def test_fetch_pins_checked_dns_and_preserves_tls_hostname() -> None:
    with patch("socket.getaddrinfo", return_value=_dns()), \
            patch("http.client.HTTPSConnection") as constructor, \
            patch("socket.create_connection") as connect:
        connection = constructor.return_value
        connection.sock = None
        connection.getresponse.return_value = _response()
        payload = source_capture.fetch_public("https://example.org/article#quote")
        connection._create_connection(("changed.example", 443))
    assert payload == (b"public article", "text/html", "https://example.org/article")
    assert constructor.call_args.args == ("example.org", 443)
    assert connect.call_args.args == (("93.184.216.34", 443),)


@pytest.mark.parametrize("target", ["http://127.0.0.1/secret", "file:///etc/passwd"])
def test_fetch_checks_redirect_before_connecting(target: str) -> None:
    with patch("socket.getaddrinfo", side_effect=[_dns(), _dns("127.0.0.1")]), \
            patch("http.client.HTTPSConnection") as connection, \
            patch("http.client.HTTPConnection") as redirected:
        connection.return_value.getresponse.return_value = _response(status=302, location=target)
        with pytest.raises(ValueError):
            source_capture.fetch_public("https://example.org/")
    redirected.assert_not_called()


def test_fetch_returns_public_redirect_final_url() -> None:
    with patch("socket.getaddrinfo", return_value=_dns()), \
            patch("http.client.HTTPSConnection") as constructor:
        constructor.return_value.sock = None
        constructor.return_value.getresponse.side_effect = [
            _response(status=302, location="/release"), _response(b"release"),
        ]
        assert source_capture.fetch_public("https://example.org/") == (
            b"release", "text/html", "https://example.org/release")


@pytest.mark.parametrize(("data", "content_type"), [
    (b"<svg></svg>", "image/svg+xml"), (b"<html>login</html>", "image/png"),
    (b"\x89PNG\r\n\x1a\ninvalid", "image/png"), (_png(), "text/html"),
])
def test_download_rejects_svg_html_and_invalid_raster(
    tmp_path: Path, data: bytes, content_type: str,
) -> None:
    with patch("socket.getaddrinfo", return_value=_dns()), \
            patch("http.client.HTTPSConnection") as connection:
        connection.return_value.sock = None
        connection.return_value.getresponse.return_value = _response(data, content_type)
        with pytest.raises(ValueError):
            source_capture.download_image("https://example.org/image", tmp_path / "image.png")
    assert list(tmp_path.iterdir()) == []


def test_download_saves_pixels_and_removes_source_metadata(tmp_path: Path) -> None:
    import cv2

    with patch("socket.getaddrinfo", return_value=_dns()), \
            patch("http.client.HTTPSConnection") as connection:
        connection.return_value.sock = None
        connection.return_value.getresponse.return_value = _response(_png(), "image/png")
        path = source_capture.download_image("https://example.org/image", tmp_path / "image.jpg")
    assert path == tmp_path / "image.png"
    assert cv2.imread(str(path)).tolist() == [[[0, 0, 255], [255, 0, 0]]]
    assert b"fixture-metadata" not in path.read_bytes()
    assert b"ai_generated" not in path.read_bytes()


def test_offline_blocks_all_capture_entrypoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHORTSMITH_BROLL_OFFLINE", "1")
    with patch("socket.getaddrinfo") as dns, patch("subprocess.run") as run:
        for call in [lambda: source_capture.fetch_public("https://example.org/"),
                     lambda: source_capture.download_image("https://example.org/i", tmp_path / "i.png"),
                     lambda: source_capture.capture_page("https://example.org/", tmp_path / "p.png")]:
            with pytest.raises(RuntimeError, match="offline"):
                call()
    dns.assert_not_called()
    run.assert_not_called()


def test_capture_passes_json_input_to_hidden_node_process(tmp_path: Path) -> None:
    def node_result(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        payload = json.loads(kwargs["input"])
        Path(payload["dest"]).write_bytes(_png())
        assert payload["quote"] == "Exact original quote"
        assert payload["selector"] == "article"
        assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert "shell" not in kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("shutil.which", return_value="node"), patch("subprocess.run", side_effect=node_result):
        result = source_capture.capture_page("https://example.org/", tmp_path / "original.png",
                                             selector="article", quote="Exact original quote")
    assert result.read_bytes() == _png()


@pytest.mark.parametrize("failure", [
    subprocess.CompletedProcess(["node"], 1, "", "Original post requires login"),
    subprocess.TimeoutExpired(["node"], 80),
])
def test_capture_reports_failure_without_output(tmp_path: Path, failure: object) -> None:
    options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
    with patch("shutil.which", return_value="node"), patch("subprocess.run", **options):
        with pytest.raises(RuntimeError, match="capture failed"):
            source_capture.capture_page("https://example.org/", tmp_path / "original.png")
    assert not (tmp_path / "original.png").exists()


def _node(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run([shutil.which("node"), "--input-type=module"], input=script,
                          cwd=Path(__file__).resolve().parent.parent, text=True,
                          capture_output=True, encoding="utf-8", timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


@pytest.mark.skipif(not shutil.which("node"), reason="Node runtime is not installed")
def test_browser_policy_rejects_private_reserved_and_internal_urls() -> None:
    result = _node("""
      import assert from 'node:assert/strict';
      import {validateUrl, assertPublic} from './scripts/capture_source.mjs';
      for (const value of ['file:///x', 'chrome://settings', 'http://localhost/',
        'https://u:p@example.org/', 'https://@example.org/', 'https://example.org/\\\\x']) {
        assert.throws(() => validateUrl(value));
      }
      for (const ip of ['127.0.0.1', '10.0.0.1', '169.254.169.254', '192.0.2.1',
        '::1', 'fc00::1', '::ffff:127.0.0.1', '2001:db8::1', 'ff02::1']) {
        assert.throws(() => assertPublic(ip));
      }
      assertPublic('8.8.8.8'); assertPublic('2606:4700:4700::1111');
    """)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not shutil.which("node"), reason="Node runtime is not installed")
@pytest.mark.parametrize(("html", "url", "expected_error"), [
    ('<title>Original release</title><article><h1>Original release</h1>'
     '<div style="height:2200px"></div><p style="color:red">Exact source quote.</p>'
     '<time>October 16, 2026</time></article>', 'https://example.org/release', ''),
    ('<title>Just a moment</title><p>Verify you are human</p>',
     'https://example.org/release', 'challenge'),
    ('<title>Blank</title>', 'https://example.org/release', 'missing or blank'),
    ('<article><p>A different quote.</p></article>', 'https://example.org/release', 'Exact quote'),
    ('<script>if(location.hostname==="example.org")location.href="https://other.example/release"</script>'
     '<article><p>Exact source quote.</p></article>', 'https://example.org/release', 'identity changed'),
    ('<script>if(location.pathname.endsWith("/42"))location.href="https://x.com/original/status/99"</script>'
     '<article><a href="/original/status/99">Post</a><p>Exact source quote.</p></article>',
     'https://x.com/original/status/42', 'identity changed'),
    ('<p>This Post was deleted</p>', 'https://x.com/original/status/42', 'deleted'),
    ('<article><a href="/original/status/42">Original post</a><p>Exact source quote.</p></article>',
     'https://x.com/original/status/42', ''),
])
def test_real_browser_screenshots_original_dom_with_offline_http_fixture(
    tmp_path: Path, html: str, url: str, expected_error: str,
) -> None:
    root = Path(__file__).resolve().parent.parent
    if not (root / "remotion/node_modules/.remotion/chrome-headless-shell").exists():
        pytest.skip("Bundled Remotion browser is not installed")
    output = tmp_path / "original.png"
    script = """
      import {syncBuiltinESMExports} from 'node:module';
      import dns from 'node:dns/promises';
      import https from 'node:https';
      import {EventEmitter} from 'node:events';
      dns.lookup = async () => [{address: '93.184.216.34', family: 4}];
      https.get = (_url, _options, callback) => {
        setImmediate(() => {
          const response = new EventEmitter(); response.statusCode = 200;
          response.headers = {'content-type': 'text/html'}; callback(response);
          response.emit('data', Buffer.from(HTML));
          response.emit('end');
        });
        return new EventEmitter();
      };
      syncBuiltinESMExports();
      const {capture} = await import('./scripts/capture_source.mjs');
      await capture(OPTIONS);
    """.replace("HTML", json.dumps(html)).replace("OPTIONS", json.dumps({"url": url, "dest": str(output),
                                      "selector": "article", "quote": "Exact source quote.",
                                      "httpTimeout": 20000, "browserTimeout": 30000}))
    result = _node(script, timeout=80)
    if expected_error:
        assert result.returncode != 0 and expected_error in result.stderr
        assert not output.exists()
        return
    assert result.returncode == 0, result.stderr
    import cv2
    image = cv2.imread(str(output))
    assert image is not None and image.shape[0] > 0 and image.shape[1] > 0
    assert image.std() > 0  # A blank capture cannot satisfy the screenshot seam.
    if "2200px" in html:
        assert image.shape[0] > 2200  # The lower quote and date must remain in the original shot.
