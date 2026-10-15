"""Shared pytest fixtures for the Ubersight test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from ubersight import dashboard


@pytest.fixture(autouse=True)
def hide_host_network_devices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the Linux wired check at an empty `/sys/class/net`.

    Without this, a test that fakes `ip route` output naming `eth0` would
    pass or fail depending on whether the machine running it has a wired
    `eth0`. Tests that need devices build their own tree and pass it in.
    """

    monkeypatch.setattr(dashboard, "LINUX_SYS_CLASS_NET", tmp_path / "no-sys-class-net")
