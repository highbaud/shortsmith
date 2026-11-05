"""Replace generated metadata only after the complete content is written."""
from __future__ import annotations

import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

log = logging.getLogger(__name__)


def atomic_write(target: Path, content: str | bytes) -> None:
    """Write UTF-8 text or bytes without creating the parent directory."""
    temporary: Path | None = None
    text = isinstance(content, str)
    try:
        with NamedTemporaryFile(
            mode="w" if text else "wb", encoding="utf-8" if text else None,
            dir=target.parent, prefix=target.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(target)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Could not remove temporary metadata file %s: %s", temporary, exc)
