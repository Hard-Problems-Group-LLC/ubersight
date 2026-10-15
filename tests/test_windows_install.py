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
    """Return where a copied launcher for `command_name` lands in `bin_dir`."""

    return bin_dir / project_install.command_path(venv_path, command_name).name


# Copied launchers ---------------------------------------------------------


def test_copied_launchers_install_every_command(tmp_path: Path) -> None:
    """Verify every console command gets an owned, byte-identical launcher copy."""

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
    """Verify a rerun refreshes owned copies in place and leaves no temp files."""

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
    """Verify an unrelated file at a launcher path is refused and left intact."""

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
    """Verify venv-only mode keeps launchers that belong to another install."""

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
    """Verify ownership detection accepts quoted paths, and any case on Windows."""

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
        """Fail the first replace onto the launcher, as a running launcher would."""
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
    """Verify venv-only mode removes this venv's copies and keeps operator files."""

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
    """Verify any home inside the checkout counts as an isolated agent home."""

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home = (repo_root / relative) if relative else repo_root

    assert install_scope.is_repo_isolated_home(home, repo_root) is isolated


def test_resolve_user_home_refuses_claude_isolated_home(
    monkeypatch, tmp_path: Path
) -> None:
    """Verify Claude Code's isolated home is refused unless explicitly allowed."""

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
    """Return stage-two arguments with standard-mode defaults and overrides."""

    values: dict[str, object] = {
        "system": False,
        "user_home": None,
        "allow_isolated_home": False,
        "mode": "standard",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_system_scope_is_rejected_on_windows(monkeypatch) -> None:
    """Verify `--system` is rejected on Windows and other scopes still resolve."""

    stage = load_stage_two()
    monkeypatch.setattr(stage, "is_windows", lambda: True)

    with pytest.raises(RuntimeError, match="not supported on Windows"):
        stage.install_scope(stage_args(system=True))
    assert stage.install_scope(stage_args()) == stage.USER_SCOPE
    assert stage.install_scope(stage_args(mode="dev")) == stage.REPO_SCOPE


def test_repo_venv_python_path_is_platform_aware(monkeypatch) -> None:
    """Verify the repository venv interpreter path uses this platform's layout."""

    stage = load_stage_two()
    monkeypatch.setattr(stage, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(stage, "repo_venv_rebuild_pending", lambda *args: False)

    assert stage.ensure_repo_venv(Path(sys.executable)) == stage.venv_python_path(
        stage.REPO_ROOT / ".venv"
    )


def test_windows_repo_runtime_uses_the_selected_interpreter(capsys) -> None:
    """Verify the Windows repo venv uses install.ps1's interpreter and notes drift."""

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
        # A trailing backslash is a separator only under Windows path rules.
        pytest.param(
            "C:\\Tools;C:\\Users\\op\\.local\\bin\\",
            "C:\\Users\\op\\.local\\bin",
            True,
            marks=windows_only,
        ),
        ('C:\\Tools;"C:\\Users\\op\\.local\\bin"', "C:\\Users\\op\\.local\\bin", True),
        ("C:\\Tools;;", "C:\\Users\\op\\.local\\bin", False),
        ("", "C:\\Users\\op\\.local\\bin", False),
    ],
)
def test_path_value_contains(value: str, directory: str, present: bool) -> None:
    """Verify PATH lookups tolerate quotes, empty entries, and trailing separators."""

    stage = load_stage_two()

    assert stage.path_value_contains(value, Path(directory), ";") is present


def test_path_value_contains_ignores_a_trailing_separator() -> None:
    """Trailing separators never hide an entry, under this platform's rules."""

    stage = load_stage_two()
    directory = Path.home() / ".local" / "bin"
    value = os.pathsep.join(["tools", str(directory) + os.sep])

    assert stage.path_value_contains(value, directory)


@windows_only
def test_path_value_contains_ignores_case_on_windows() -> None:
    """Verify PATH lookups ignore case on Windows."""

    stage = load_stage_two()

    assert stage.path_value_contains(
        "c:\\users\\OP\\.LOCAL\\bin", Path("C:\\Users\\op\\.local\\bin"), ";"
    )


def test_append_path_entry() -> None:
    """Verify appending to PATH drops a trailing separator and handles empty values."""

    stage = load_stage_two()

    assert stage.append_path_entry("A;B;", Path("C"), ";") == "A;B;C"
    assert stage.append_path_entry("", Path("C"), ";") == "C"


class FakeUserPath:
    """In-memory stand-in for the HKCU PATH registry value."""

    def __init__(self, value: str, kind: int = 2) -> None:
        """Start with one PATH value and registry type and no recorded writes."""
        self.value = value
        self.kind = kind
        self.writes: list[tuple[str, int]] = []
        self.broadcasts = 0

    def read(self) -> tuple[str, int]:
        """Return the stored PATH value and registry type."""
        return self.value, self.kind

    def write(self, value: str, kind: int) -> None:
        """Record a PATH write and keep the new value."""
        self.writes.append((value, kind))
        self.value = value

    def broadcast(self) -> None:
        """Count environment-change broadcasts."""
        self.broadcasts += 1


def test_launcher_dir_is_added_to_user_path_preserving_type(
    monkeypatch, tmp_path: Path
) -> None:
    """Verify a missing launcher directory is appended once, keeping the value type."""

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
    """Verify opt-out and in-checkout launcher directories never touch PATH."""

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
    """Verify POSIX hosts only report whether the launcher directory is on PATH."""

    stage = load_stage_two()
    monkeypatch.setattr(stage, "is_windows", lambda: False)
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", str(tmp_path / "bin")]))

    assert "is on PATH" in stage.ensure_launcher_dir_on_path(
        tmp_path / "bin", modify=True
    )
    missing = stage.ensure_launcher_dir_on_path(tmp_path / "other", modify=True)
    assert "export PATH" in missing


def test_managed_hooks_are_converted_to_lf(tmp_path: Path) -> None:
    """Verify only the managed hook wrappers are converted to LF line endings."""

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


# Venv-only curses support ---------------------------------------------------


def test_windows_curses_requirement_matches_pyproject() -> None:
    """Verify the Python 3.9-safe scanner reads what tomllib reads."""

    tomllib = pytest.importorskip("tomllib")
    stage = load_stage_two()
    with (ROOT / "pyproject.toml").open("rb") as handle:
        declared = tomllib.load(handle)["project"]["dependencies"]
    expected = next(
        requirement.split(";", 1)[0].strip()
        for requirement in declared
        if requirement.startswith("windows-curses")
    )

    assert stage.project_dependencies(ROOT) == declared
    assert stage.windows_curses_requirement(ROOT) == expected
    assert expected.startswith("windows-curses>=")


def test_project_dependency_scanner_reads_only_project_strings(
    tmp_path: Path,
) -> None:
    """Verify comments, literal strings, and other tables do not confuse the scan."""

    stage = load_stage_two()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[build-system]\n"
        'requires = ["setuptools>=69"]\n'
        "\n"
        "[project]\n"
        'name = "demo"\n'
        "dependencies = [\n"
        "  # pinned for [reasons]\n"
        "  \"Windows_Curses >= 2.5; sys_platform == 'win32'\",  # see #12\n"
        "  'literal-pkg[extra]>=1',\n"
        "]\n"
        "\n"
        "[project.optional-dependencies]\n"
        'dev = ["pytest"]\n',
        encoding="utf-8",
    )

    assert stage.project_dependencies(tmp_path) == [
        "Windows_Curses >= 2.5; sys_platform == 'win32'",
        "literal-pkg[extra]>=1",
    ]
    assert stage.windows_curses_requirement(tmp_path) == "Windows_Curses >= 2.5"

    pyproject.write_text('[project]\nname = "demo"\n', encoding="utf-8")
    assert stage.project_dependencies(tmp_path) == []
    with pytest.raises(RuntimeError, match="does not declare the windows-curses"):
        stage.windows_curses_requirement(tmp_path)


def test_windows_curses_is_installed_only_for_windows_venv_only(
    monkeypatch,
) -> None:
    """Verify only a Windows venv-only `.venv` gets the curses wheel added."""

    stage = load_stage_two()
    commands: list[list[str]] = []
    monkeypatch.setattr(
        stage, "run", lambda command, **kwargs: commands.append(list(command))
    )
    venv_python = Path("venv-python")
    requirement = stage.windows_curses_requirement(stage.REPO_ROOT)

    monkeypatch.setattr(stage, "is_windows", lambda: False)
    assert not stage.ensure_windows_venv_only_curses(venv_python, "venv-only")
    monkeypatch.setattr(stage, "is_windows", lambda: True)
    assert not stage.ensure_windows_venv_only_curses(venv_python, "dev")
    assert commands == []

    assert stage.ensure_windows_venv_only_curses(venv_python, "venv-only")
    assert commands == [[str(venv_python), "-m", "pip", "install", requirement]]


def test_windows_venv_only_install_adds_curses_before_verifying(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    """Verify Windows venv-only installs the curses wheel before the curses check."""

    stage = load_stage_two()
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(stage, "is_windows", lambda: True)
    monkeypatch.setattr(
        stage, "windows_repo_runtime_python", lambda: Path(sys.executable)
    )
    monkeypatch.setattr(stage, "ensure_repo_venv", lambda runtime: venv_python)
    monkeypatch.setattr(
        stage, "run", lambda command, **kwargs: events.append(("run", list(command)))
    )
    monkeypatch.setattr(
        stage,
        "install_project",
        lambda python, venv, mode, scope, bin_dir: events.append(("project", mode)),
    )
    monkeypatch.setattr(stage, "install_git_hooks", lambda python: None)
    monkeypatch.setattr(stage, "normalize_git_hook_line_endings", lambda: ())
    monkeypatch.setattr(
        stage, "verify_install", lambda python: events.append(("verify", python))
    )

    status = stage.main(
        [
            "--mode",
            "venv-only",
            "--force-direct-run",
            "--skip-submodule-init",
            "--user-home",
            str(tmp_path / "home"),
        ]
    )

    assert status == 0, capsys.readouterr().err
    requirement = stage.windows_curses_requirement(stage.REPO_ROOT)
    assert events == [
        ("run", [str(venv_python), "-m", "pip", "install", requirement]),
        ("project", "venv-only"),
        ("verify", venv_python),
    ]


def test_windows_curses_failure_gives_a_windows_remedy(monkeypatch) -> None:
    """Verify a missing `_curses` on Windows points at windows-curses, not ncurses."""

    stage = load_stage_two()
    monkeypatch.setattr(stage, "is_windows", lambda: True)

    def missing_curses(command, **kwargs):
        """Simulate stock Windows CPython, which has no `_curses` module."""

        raise subprocess.CalledProcessError(
            1, command, stderr="ModuleNotFoundError: No module named '_curses'"
        )

    monkeypatch.setattr(stage, "run", missing_curses)

    with pytest.raises(stage.CursesSupportError) as raised:
        stage.verify_curses_support(Path("C:/venv/Scripts/python.exe"))

    message = str(raised.value)
    assert "No module named '_curses'" in message
    assert "windows-curses" in message
    assert ".\\install.ps1" in message
    assert "-m pip install windows-curses" in message
    for posix_advice in ("install_prerequisites.sh", "ncurses-devel", "install.sh"):
        assert posix_advice not in message


# Rebuilding a venv that is in use ------------------------------------------


def make_stage_venv(stage, tmp_path: Path) -> Path:
    """Create a fake venv with an interpreter in both layouts and a `Lib` dir."""

    venv_path = tmp_path / "venv"
    scripts = venv_path / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"python")
    interpreter = stage.venv_python_path(venv_path)
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_bytes(b"python")
    (venv_path / "Lib").mkdir()
    return venv_path


def test_interpreter_change_never_clears_a_venv_in_use(
    monkeypatch, tmp_path: Path
) -> None:
    """Verify a busy venv stops the rebuild before `venv --clear` deletes anything."""

    stage = load_stage_two()
    venv_path = make_stage_venv(stage, tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(stage, "is_windows", lambda: True)
    # Each interpreter reports itself, so the venv and the new base differ.
    monkeypatch.setattr(stage, "interpreter_base_identity", lambda python: python)
    monkeypatch.setattr(stage, "file_in_use", lambda path: path.name == "python.exe")
    monkeypatch.setattr(
        stage, "run", lambda command, **kwargs: commands.append(list(command))
    )

    with pytest.raises(stage.VenvInUseError) as raised:
        stage.ensure_virtualenv(tmp_path / "new-python.exe", venv_path)

    message = str(raised.value)
    assert "python.exe is in use" in message
    assert "Nothing was changed" in message
    assert "press Q in each pane" in message
    assert "rerun the installer" in message
    assert commands == []
    assert (venv_path / "Lib").is_dir()


def test_interpreter_change_rebuilds_an_idle_venv(monkeypatch, tmp_path: Path) -> None:
    """Verify an idle venv, or any venv on POSIX, is still rebuilt as before."""

    stage = load_stage_two()
    venv_path = make_stage_venv(stage, tmp_path)
    base_python = tmp_path / "new-python.exe"
    commands: list[list[str]] = []
    monkeypatch.setattr(stage, "interpreter_base_identity", lambda python: python)
    monkeypatch.setattr(
        stage, "run", lambda command, **kwargs: commands.append(list(command))
    )

    monkeypatch.setattr(stage, "is_windows", lambda: True)
    stage.ensure_virtualenv(base_python, venv_path)
    # POSIX hosts can delete files in use, so they are never checked.
    monkeypatch.setattr(stage, "is_windows", lambda: False)
    monkeypatch.setattr(stage, "file_in_use", lambda path: True)
    stage.ensure_virtualenv(base_python, venv_path)

    clear = [str(base_python), "-m", "venv", "--clear", str(venv_path)]
    assert commands == [clear, clear]


def test_repo_venv_is_checked_for_use_only_before_a_rebuild(monkeypatch) -> None:
    """Verify `.venv` is checked before dev_setup.py would clear it, and only then."""

    stage = load_stage_two()
    commands: list[list[str]] = []
    checked: list[Path] = []
    monkeypatch.setattr(stage, "is_windows", lambda: True)
    monkeypatch.setattr(
        stage, "run", lambda command, **kwargs: commands.append(list(command))
    )

    def busy(venv_path: Path) -> None:
        """Report the venv as held open by a running dashboard."""

        checked.append(venv_path)
        raise stage.VenvInUseError("in use")

    monkeypatch.setattr(stage, "ensure_venv_not_in_use", busy)

    monkeypatch.setattr(stage, "repo_venv_rebuild_pending", lambda *args: False)
    stage.ensure_repo_venv(Path(sys.executable))
    assert checked == []
    assert len(commands) == 1
    assert commands[0][1].endswith("dev_setup.py")

    commands.clear()
    monkeypatch.setattr(stage, "repo_venv_rebuild_pending", lambda *args: True)
    with pytest.raises(stage.VenvInUseError):
        stage.ensure_repo_venv(Path(sys.executable))
    assert checked == [stage.REPO_ROOT / ".venv"]
    assert commands == []


def test_repo_venv_rebuild_pending_follows_dev_setup_version_rule(
    monkeypatch, tmp_path: Path
) -> None:
    """Verify a rebuild is predicted exactly when dev_setup.py would clear `.venv`."""

    stage = load_stage_two()
    venv_path = tmp_path / ".venv"
    runtime = tmp_path / "runtime" / "python.exe"
    reports: dict[Path, str | None] = {}
    monkeypatch.setattr(stage, "python_version_report", reports.get)

    assert not stage.repo_venv_rebuild_pending(venv_path, runtime)
    venv_python = stage.venv_python_path(venv_path)
    venv_python.parent.mkdir(parents=True)
    venv_python.write_bytes(b"python")
    reports.update({venv_python: "Python 3.12.10", runtime: "Python 3.12.10"})
    assert not stage.repo_venv_rebuild_pending(venv_path, runtime)
    reports[runtime] = "Python 3.14.6"
    assert stage.repo_venv_rebuild_pending(venv_path, runtime)
    # dev_setup.py fails its own version probe before deleting anything.
    reports[venv_python] = None
    assert not stage.repo_venv_rebuild_pending(venv_path, runtime)


def test_python_version_report_reads_a_real_interpreter(tmp_path: Path) -> None:
    """Verify the version probe reports this interpreter and tolerates failure."""

    stage = load_stage_two()

    assert stage.python_version_report(Path(sys.executable)) == (
        "Python " + ".".join(str(part) for part in sys.version_info[:3])
    )
    assert stage.python_version_report(tmp_path / "missing-python") is None


@windows_only
def test_running_venv_interpreter_blocks_rebuild_on_real_windows(
    tmp_path: Path,
) -> None:
    """Verify a running `Scripts\\python.exe` is detected without touching it."""

    stage = load_stage_two()
    venv_path = make_stage_venv(stage, tmp_path)
    python = venv_path / "Scripts" / "python.exe"
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    python.write_bytes((system32 / "PING.EXE").read_bytes())
    assert not stage.file_in_use(python)
    running = subprocess.Popen(
        [str(python), "-n", "30", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        if running.poll() is not None:
            pytest.skip("copied system executable did not keep running")
        # Renaming would succeed here; Windows only refuses delete and write.
        assert stage.file_in_use(python)
        with pytest.raises(stage.VenvInUseError, match="python.exe is in use"):
            stage.ensure_venv_not_in_use(venv_path)
        assert python.is_file()
        assert (venv_path / "Lib").is_dir()
    finally:
        running.kill()
        running.wait(timeout=30)

    deadline = time.monotonic() + 10
    while stage.file_in_use(python) and time.monotonic() < deadline:
        time.sleep(0.1)
    stage.ensure_venv_not_in_use(venv_path)
