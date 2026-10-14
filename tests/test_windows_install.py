"""Tests for the Windows install path: copied launchers, PATH, and stage two.

Most tests force the Windows code paths with fakes so they run everywhere.
Tests marked ``windows_only`` exercise real Windows behavior, such as
replacing a launcher that is currently running.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ubersight import install_scope, project_install

ROOT = Path(__file__).resolve().parents[1]
windows_only = pytest.mark.skipif(os.name != "nt", reason="requires native Windows")


def load_stage_two():
    """Load `scripts/install-stage-2.py` for focused tests."""

    module_path = ROOT / "scripts" / "install-stage-2.py"
    spec = importlib.util.spec_from_file_location("install_stage_2_win", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_venv(tmp_path: Path, name: str = "venv") -> Path:
    """Create a fake venv whose launchers embed its Python path like pip's."""

    venv_path = tmp_path / name
    python = project_install.venv_python(venv_path)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    for command_name in project_install.COMMANDS:
        target = project_install.command_path(venv_path, command_name)
        target.write_bytes(
            b"MZ-launcher-stub\n#!" + str(python).encode("utf-8") + b"\nPK-zip"
        )
    return venv_path


def launcher_for(bin_dir: Path, venv_path: Path, command_name: str) -> Path:
    return bin_dir / project_install.command_path(venv_path, command_name).name


# Copied launchers ---------------------------------------------------------


def test_copied_launchers_install_every_command(tmp_path: Path) -> None:
    venv_path = make_venv(tmp_path)
    bin_dir = tmp_path / "bin"

    launchers = project_install.manage_launchers(
        mode="standard", venv_path=venv_path, bin_dir=bin_dir, copy_launchers=True
    )

    assert len(launchers) == len(project_install.COMMANDS)
    for command_name in project_install.COMMANDS:
        launcher = launcher_for(bin_dir, venv_path, command_name)
        assert not launcher.is_symlink()
        assert launcher.read_bytes() == (
            project_install.command_path(venv_path, command_name).read_bytes()
        )
        assert project_install.is_owned_copied_launcher(launcher, venv_path)


def test_copied_launchers_are_idempotent_and_refresh_owned_copies(
    tmp_path: Path,
) -> None:
    venv_path = make_venv(tmp_path)
    bin_dir = tmp_path / "bin"
    project_install.manage_launchers(
        mode="standard", venv_path=venv_path, bin_dir=bin_dir, copy_launchers=True
    )
    target = project_install.command_path(venv_path, "ubersight")
    target.write_bytes(target.read_bytes() + b"-upgraded")

    project_install.manage_launchers(
        mode="standard", venv_path=venv_path, bin_dir=bin_dir, copy_launchers=True
    )

    launcher = launcher_for(bin_dir, venv_path, "ubersight")
    assert launcher.read_bytes().endswith(b"-upgraded")
    assert not list(bin_dir.glob("*.tmp"))


def test_copied_launcher_refuses_files_that_are_not_python_launchers(
    tmp_path: Path,
) -> None:
    venv_path = make_venv(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    unmanaged = launcher_for(bin_dir, venv_path, "ubersight")
    unmanaged.write_bytes(b"somebody else's tool")
    target = project_install.command_path(venv_path, "ubersight")

    with pytest.raises(RuntimeError, match="unmanaged launcher"):
        project_install.ensure_copied_launcher(unmanaged, target, venv_path)
    assert unmanaged.read_bytes() == b"somebody else's tool"
    assert not project_install.is_python_console_launcher(unmanaged)


def test_launcher_from_another_install_mode_is_repointed(tmp_path: Path) -> None:
    """Switching standard and dev installs re-points launchers, as on POSIX."""

    venv_path = make_venv(tmp_path)
    other_venv = make_venv(tmp_path, "standard-venv")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = launcher_for(bin_dir, venv_path, "ubersight-stop")
    shutil.copy2(project_install.command_path(other_venv, "ubersight-stop"), launcher)
    target = project_install.command_path(venv_path, "ubersight-stop")

    assert project_install.is_python_console_launcher(launcher)
    assert not project_install.is_owned_copied_launcher(launcher, venv_path)
    project_install.ensure_copied_launcher(launcher, target, venv_path)

    assert launcher.read_bytes() == target.read_bytes()
    assert project_install.is_owned_copied_launcher(launcher, venv_path)


def test_venv_only_leaves_launchers_from_other_installs(tmp_path: Path) -> None:
    venv_path = make_venv(tmp_path)
    other_venv = make_venv(tmp_path, "standard-venv")
    bin_dir = tmp_path / "bin"
    project_install.manage_launchers(
        mode="standard", venv_path=other_venv, bin_dir=bin_dir, copy_launchers=True
    )

    project_install.manage_launchers(
        mode="venv-only", venv_path=venv_path, bin_dir=bin_dir, copy_launchers=True
    )

    assert len(list(bin_dir.iterdir())) == len(project_install.COMMANDS)


def test_owned_launcher_detection_handles_quoted_paths(tmp_path: Path) -> None:
    venv_path = tmp_path / "my venv"
    python = project_install.venv_python(venv_path)
    launcher = tmp_path / "launcher"
    launcher.write_bytes(b'stub#!"' + str(python).upper().encode() + b'"\nzip')

    if os.name == "nt":
        assert project_install.is_owned_copied_launcher(launcher, venv_path)
    launcher.write_bytes(b'stub#!"' + str(python).encode() + b'"\nzip')
    assert project_install.is_owned_copied_launcher(launcher, venv_path)
    assert not project_install.is_owned_copied_launcher(tmp_path / "missing", venv_path)


def test_in_use_launcher_is_renamed_aside(monkeypatch, tmp_path: Path) -> None:
    """A running launcher cannot be overwritten on Windows, only renamed."""

    venv_path = make_venv(tmp_path)
    bin_dir = tmp_path / "bin"
    project_install.manage_launchers(
        mode="standard", venv_path=venv_path, bin_dir=bin_dir, copy_launchers=True
    )
    launcher = launcher_for(bin_dir, venv_path, "ubersight")
    target = project_install.command_path(venv_path, "ubersight")
    target.write_bytes(target.read_bytes() + b"-v2")
    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def replace_blocked_once(source, destination):
        calls.append((Path(source).name, Path(destination).name))
        if Path(destination) == launcher and Path(source).name.endswith(".tmp"):
            if len(calls) == 1:
                raise PermissionError("launcher is running")
        return real_replace(source, destination)

    monkeypatch.setattr(project_install.os, "replace", replace_blocked_once)

    project_install.ensure_copied_launcher(launcher, target, venv_path)

    assert launcher.read_bytes().endswith(b"-v2")
    assert calls[1][0] == launcher.name
    assert calls[1][1].startswith(f"{launcher.name}.old-")
    assert project_install.retired_launcher_paths(launcher) == ()


def test_venv_only_removes_owned_copies_but_keeps_others(tmp_path: Path) -> None:
    venv_path = make_venv(tmp_path)
    bin_dir = tmp_path / "bin"
    project_install.manage_launchers(
        mode="standard", venv_path=venv_path, bin_dir=bin_dir, copy_launchers=True
    )
    keep = launcher_for(bin_dir, venv_path, "ubersight-stop")
    keep.write_bytes(b"replaced by the operator")

    assert (
        project_install.manage_launchers(
            mode="venv-only",
            venv_path=venv_path,
            bin_dir=bin_dir,
            copy_launchers=True,
        )
        == ()
    )

    assert sorted(path.name for path in bin_dir.iterdir()) == [keep.name]


@windows_only
def test_running_launcher_is_replaced_on_real_windows(tmp_path: Path) -> None:
    """Replace a launcher while its process is still running."""

    venv_path = make_venv(tmp_path)
    marker = b"#!" + str(project_install.venv_python(venv_path)).encode("utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = launcher_for(bin_dir, venv_path, "ubersight")
    system_ping = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    launcher.write_bytes((system_ping / "PING.EXE").read_bytes() + b"\n" + marker)
    running = subprocess.Popen(
        [str(launcher), "-n", "30", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        if running.poll() is not None:
            pytest.skip("copied system executable did not keep running")
        with pytest.raises(PermissionError):
            launcher.unlink()
        target = project_install.command_path(venv_path, "ubersight")

        project_install.ensure_copied_launcher(launcher, target, venv_path)

        assert launcher.read_bytes() == target.read_bytes()
        assert len(project_install.retired_launcher_paths(launcher)) == 1
    finally:
        running.kill()
        running.wait(timeout=30)

    project_install.ensure_copied_launcher(
        launcher, project_install.command_path(venv_path, "ubersight"), venv_path
    )
    assert project_install.retired_launcher_paths(launcher) == ()


# Isolated homes -----------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "isolated"),
    [
        (".codex-home", True),
        (".local/claude/HOME", True),
        ("", True),
        ("../operator", False),
    ],
)
def test_homes_inside_the_checkout_are_isolated(
    tmp_path: Path, relative: str, isolated: bool
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home = (repo_root / relative) if relative else repo_root

    assert install_scope.is_repo_isolated_home(home, repo_root) is isolated


def test_resolve_user_home_refuses_claude_isolated_home(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "repo"
    isolated = repo_root / ".local" / "claude" / "HOME"
    isolated.mkdir(parents=True)
    monkeypatch.setattr(install_scope, "current_home", lambda: isolated.resolve())

    with pytest.raises(RuntimeError, match="inside this checkout"):
        install_scope.resolve_user_home(repo_root)
    assert (
        install_scope.resolve_user_home(repo_root, allow_isolated_home=True)
        == isolated.resolve()
    )
    assert (
        install_scope.describe_user_home_source(repo_root, isolated.resolve(), None)
        == "current repo-local isolated AI home"
    )


# Stage two ----------------------------------------------------------------


def stage_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "system": False,
        "user_home": None,
        "allow_isolated_home": False,
        "mode": "standard",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_system_scope_is_rejected_on_windows(monkeypatch) -> None:
    stage = load_stage_two()
    monkeypatch.setattr(stage, "is_windows", lambda: True)

    with pytest.raises(RuntimeError, match="not supported on Windows"):
        stage.install_scope(stage_args(system=True))
    assert stage.install_scope(stage_args()) == stage.USER_SCOPE
    assert stage.install_scope(stage_args(mode="dev")) == stage.REPO_SCOPE


def test_repo_venv_python_path_is_platform_aware(monkeypatch) -> None:
    stage = load_stage_two()
    monkeypatch.setattr(stage, "run", lambda *args, **kwargs: None)

    assert stage.ensure_repo_venv(Path(sys.executable)) == stage.venv_python_path(
        stage.REPO_ROOT / ".venv"
    )


def test_windows_repo_runtime_uses_the_selected_interpreter(capsys) -> None:
    stage = load_stage_two()

    assert stage.windows_repo_runtime_python() == Path(sys.executable).resolve()
    current = ".".join(str(part) for part in sys.version_info[:3])
    pinned = stage.load_python_environment_config(stage.REPO_ROOT).runtime
    output = capsys.readouterr().out
    if current != pinned.base_version:
        assert f"pinned runtime {pinned.base_version} is not installed" in output


@pytest.mark.parametrize(
    ("value", "directory", "present"),
    [
        ("C:\\Tools;C:\\Users\\op\\.local\\bin", "C:\\Users\\op\\.local\\bin", True),
        ("C:\\Tools;C:\\Users\\op\\.local\\bin\\", "C:\\Users\\op\\.local\\bin", True),
        ('C:\\Tools;"C:\\Users\\op\\.local\\bin"', "C:\\Users\\op\\.local\\bin", True),
        ("C:\\Tools;;", "C:\\Users\\op\\.local\\bin", False),
        ("", "C:\\Users\\op\\.local\\bin", False),
    ],
)
def test_path_value_contains(value: str, directory: str, present: bool) -> None:
    stage = load_stage_two()

    assert stage.path_value_contains(value, Path(directory), ";") is present


@windows_only
def test_path_value_contains_ignores_case_on_windows() -> None:
    stage = load_stage_two()

    assert stage.path_value_contains(
        "c:\\users\\OP\\.LOCAL\\bin", Path("C:\\Users\\op\\.local\\bin"), ";"
    )


def test_append_path_entry() -> None:
    stage = load_stage_two()

    assert stage.append_path_entry("A;B;", Path("C"), ";") == "A;B;C"
    assert stage.append_path_entry("", Path("C"), ";") == "C"


class FakeUserPath:
    """In-memory stand-in for the HKCU PATH registry value."""

    def __init__(self, value: str, kind: int = 2) -> None:
        self.value = value
        self.kind = kind
        self.writes: list[tuple[str, int]] = []
        self.broadcasts = 0

    def read(self) -> tuple[str, int]:
        return self.value, self.kind

    def write(self, value: str, kind: int) -> None:
        self.writes.append((value, kind))
        self.value = value

    def broadcast(self) -> None:
        self.broadcasts += 1


def test_launcher_dir_is_added_to_user_path_preserving_type(
    monkeypatch, tmp_path: Path
) -> None:
    stage = load_stage_two()
    monkeypatch.setattr(stage, "is_windows", lambda: True)
    bin_dir = tmp_path / "home" / ".local" / "bin"
    registry = FakeUserPath("C:\\Tools;%USERPROFILE%\\scoop\\shims", kind=2)

    status = stage.ensure_launcher_dir_on_path(bin_dir, modify=True, user_path=registry)

    assert registry.writes == [(f"C:\\Tools;%USERPROFILE%\\scoop\\shims;{bin_dir}", 2)]
    assert registry.broadcasts == 1
    assert "Added" in status

    again = stage.ensure_launcher_dir_on_path(bin_dir, modify=True, user_path=registry)
    assert len(registry.writes) == 1
    assert "is on the user PATH" in again


def test_launcher_dir_is_not_added_when_opted_out_or_inside_checkout(
    monkeypatch, tmp_path: Path
) -> None:
    stage = load_stage_two()
    monkeypatch.setattr(stage, "is_windows", lambda: True)
    registry = FakeUserPath("C:\\Tools")

    opted_out = stage.ensure_launcher_dir_on_path(
        tmp_path / "bin", modify=False, user_path=registry
    )
    sandboxed = stage.ensure_launcher_dir_on_path(
        stage.REPO_ROOT / ".local" / "claude" / "HOME" / ".local" / "bin",
        modify=True,
        user_path=registry,
    )

    assert registry.writes == []
    assert "--no-modify-path" in opted_out
    assert "inside this checkout" in sandboxed


def test_posix_launcher_dir_is_reported_not_modified(
    monkeypatch, tmp_path: Path
) -> None:
    stage = load_stage_two()
    monkeypatch.setattr(stage, "is_windows", lambda: False)
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", str(tmp_path / "bin")]))

    assert "is on PATH" in stage.ensure_launcher_dir_on_path(
        tmp_path / "bin", modify=True
    )
    missing = stage.ensure_launcher_dir_on_path(tmp_path / "other", modify=True)
    assert "export PATH" in missing


def test_managed_hooks_are_converted_to_lf(tmp_path: Path) -> None:
    stage = load_stage_two()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_bytes(b"#!/usr/bin/env bash\r\nset -euo pipefail\r\n")
    (hooks / "pre-push").write_bytes(b"#!/usr/bin/env bash\nexit 0\n")
    (hooks / "post-merge").write_bytes(b"leave\r\nalone\r\n")

    converted = stage.normalize_git_hook_line_endings(hooks)

    assert converted == (hooks / "pre-commit",)
    assert (hooks / "pre-commit").read_bytes() == (
        b"#!/usr/bin/env bash\nset -euo pipefail\n"
    )
    assert (hooks / "post-merge").read_bytes() == b"leave\r\nalone\r\n"
    assert stage.normalize_git_hook_line_endings(tmp_path / "missing") == ()
