"""Atomic local-file helpers shared by Ubersight writers and readers.

Ubersight writers publish JSON by writing a temporary sibling file and then
replacing the target with ``os.replace``, so readers never observe a partial
document. On Windows that replacement raises ``PermissionError`` while any
other process has the target open, for example while the dashboard is reading
the status file at the same instant an agent writes it. These helpers retry
that transient sharing violation briefly instead of failing the write, and
give readers the same short tolerance.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Callable, Sequence

# Backoff before each retry; the sum stays close to one second so a stuck
# reader cannot stall a status write for long.
REPLACE_RETRY_DELAYS_SECONDS: tuple[float, ...] = (
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.25,
    0.4,
)
READ_RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.01, 0.02, 0.05)


def _retries_permission_errors() -> bool:
    """Return whether this platform treats ``PermissionError`` as transient."""

    return os.name == "nt"


def replace_with_retry(
    source: Path,
    target: Path,
    *,
    retry_permission_errors: bool | None = None,
    delays: Sequence[float] = REPLACE_RETRY_DELAYS_SECONDS,
    replace: Callable[[Path, Path], None] = os.replace,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Replace ``target`` with ``source``, retrying Windows sharing violations.

    On POSIX a ``PermissionError`` is a real permission problem, so it is
    raised immediately unless ``retry_permission_errors`` forces retries.
    """

    if retry_permission_errors is None:
        retry_permission_errors = _retries_permission_errors()
    pending_delays = list(delays) if retry_permission_errors else []
    while True:
        try:
            replace(source, target)
            return
        except PermissionError:
            if not pending_delays:
                raise
            sleep(pending_delays.pop(0))


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    retry_permission_errors: bool | None = None,
) -> None:
    """Atomically replace ``path`` with ``text`` using LF line endings.

    The temporary file lives beside the target so the final replacement stays
    on one filesystem. It is removed when the write or replacement fails.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp_path.open("w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
        replace_with_retry(
            tmp_path, path, retry_permission_errors=retry_permission_errors
        )
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


def read_text_with_retry(
    path: Path,
    *,
    encoding: str = "utf-8",
    retry_permission_errors: bool | None = None,
    delays: Sequence[float] = READ_RETRY_DELAYS_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Read ``path``, briefly retrying a Windows replacement in progress."""

    if retry_permission_errors is None:
        retry_permission_errors = _retries_permission_errors()
    pending_delays = list(delays) if retry_permission_errors else []
    while True:
        try:
            return path.read_text(encoding=encoding)
        except PermissionError:
            if not pending_delays:
                raise
            sleep(pending_delays.pop(0))
