"""Tests for the cached + throttled HTTP layer in scripts/gen_broll.py.

We import the module under test by adding scripts/ to sys.path. The module
exposes _http_get, _cache_path_for, and reads a module-level _LAST_FETCH_AT
for throttle bookkeeping.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import gen_broll  # noqa: E402


def test_cache_path_is_stable_per_url(tmp_path: Path) -> None:
    """Same URL -> same sha1 path; different URLs -> different paths."""
    url_a = "https://commons.wikimedia.org/foo.svg"
    url_b = "https://commons.wikimedia.org/bar.svg"
    a1 = gen_broll._cache_path_for(url_a)
    a2 = gen_broll._cache_path_for(url_a)
    b = gen_broll._cache_path_for(url_b)
    assert a1 == a2
    assert a1 != b
    # Extension hint preserved.
    assert a1.suffix == ".svg"


def test_cache_path_preserves_known_extensions() -> None:
    for ext in (".svg", ".png", ".jpg", ".webp", ".json"):
        p = gen_broll._cache_path_for(f"https://x.test/asset{ext}")
        assert p.suffix == ext


def test_cache_hit_short_circuits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cached file returns immediately without touching urlopen."""
    monkeypatch.setattr(gen_broll, "_CACHE_DIR", tmp_path)

    url = "https://commons.wikimedia.org/cached.svg"
    cached_payload = b"<svg>cached</svg>"
    gen_broll._cache_path_for(url).write_bytes(cached_payload)

    with patch("urllib.request.urlopen") as mock_open:
        data = gen_broll._http_get(url)

    assert data == cached_payload
    mock_open.assert_not_called()


def test_offline_returns_none_for_uncached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHORTSMITH_BROLL_OFFLINE=1 must never hit the network."""
    monkeypatch.setattr(gen_broll, "_CACHE_DIR", tmp_path)
    monkeypatch.setenv("SHORTSMITH_BROLL_OFFLINE", "1")

    with patch("urllib.request.urlopen") as mock_open:
        data = gen_broll._http_get("https://x.test/never-fetched.svg")

    assert data is None
    mock_open.assert_not_called()


def test_nocache_forces_refetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHORTSMITH_BROLL_NOCACHE=1 must bypass the cache even if a file exists."""
    monkeypatch.setattr(gen_broll, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(gen_broll, "_THROTTLE_SECONDS", 0.0)
    monkeypatch.setenv("SHORTSMITH_BROLL_NOCACHE", "1")

    url = "https://commons.wikimedia.org/file.svg"
    gen_broll._cache_path_for(url).write_bytes(b"stale")

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"<svg>fresh</svg>"
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    with patch("urllib.request.urlopen", return_value=mock_resp):
        data = gen_broll._http_get(url)

    assert data == b"<svg>fresh</svg>"


def test_successful_fetch_writes_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 response gets stored to the cache for future hits."""
    monkeypatch.setattr(gen_broll, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(gen_broll, "_THROTTLE_SECONDS", 0.0)
    # Make sure offline / nocache aren't set
    monkeypatch.delenv("SHORTSMITH_BROLL_OFFLINE", raising=False)
    monkeypatch.delenv("SHORTSMITH_BROLL_NOCACHE", raising=False)

    payload = b"<svg>once</svg>"
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = payload
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = None

    url = "https://commons.wikimedia.org/fresh.svg"
    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        first = gen_broll._http_get(url)
        # Second call should be served from cache without urlopen.
        second = gen_broll._http_get(url)

    assert first == payload
    assert second == payload
    assert mock_open.call_count == 1
    assert gen_broll._cache_path_for(url).read_bytes() == payload


def test_retry_on_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """429 backs off and retries; eventually returns the success payload."""
    import urllib.error

    monkeypatch.setattr(gen_broll, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(gen_broll, "_THROTTLE_SECONDS", 0.0)
    monkeypatch.delenv("SHORTSMITH_BROLL_OFFLINE", raising=False)
    monkeypatch.delenv("SHORTSMITH_BROLL_NOCACHE", raising=False)

    # No-op sleep so the test doesn't actually wait.
    monkeypatch.setattr("time.sleep", lambda s: None)

    success = MagicMock()
    success.status = 200
    success.read.return_value = b"<svg>ok</svg>"
    success.__enter__.return_value = success
    success.__exit__.return_value = None

    rate_err = urllib.error.HTTPError(
        url="https://x.test", code=429, msg="Too Many", hdrs=None, fp=None,
    )

    call = {"n": 0}

    def fake_open(req, timeout=20):
        call["n"] += 1
        if call["n"] < 3:
            raise rate_err
        return success

    with patch("urllib.request.urlopen", side_effect=fake_open):
        data = gen_broll._http_get("https://x.test/asset.svg", max_retries=4)

    assert data == b"<svg>ok</svg>"
    assert call["n"] == 3


def test_non_retriable_status_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 404 fails fast — no retries."""
    import urllib.error

    monkeypatch.setattr(gen_broll, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(gen_broll, "_THROTTLE_SECONDS", 0.0)
    monkeypatch.delenv("SHORTSMITH_BROLL_OFFLINE", raising=False)
    monkeypatch.delenv("SHORTSMITH_BROLL_NOCACHE", raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)

    err = urllib.error.HTTPError(
        url="https://x.test", code=404, msg="Not Found", hdrs=None, fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=err) as mock_open:
        data = gen_broll._http_get("https://x.test/missing.svg", max_retries=5)

    assert data is None
    assert mock_open.call_count == 1
