"""Project install hook for Ubersight package and launcher installs.

Launchers make the console commands available from a directory on `PATH`.
POSIX hosts use symlinks into the managed virtual environment. Windows uses
copies of pip's `.exe` launchers instead. Symlinks there need Developer Mode
or elevation, and a copied launcher still runs its own virtual environment
because pip embeds the environment's absolute Python path in it. That
embedded path also identifies which copies this installer owns.
"""

from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Sequence

COMMANDS = (
    "ubersight",
    "ubersight-location-memory",
    "ubersight-probe-location",
    "ubersight-stop",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the managed install hook contract."""

    parser = argparse.ArgumentParser(
        description="Install Ubersight into a managed virtual environment."
    )
    parser.add_argument(
        "--mode", choices=("dev", "standard", "venv-only"), required=True
    )
    parser.add_argument("--scope", choices=("repo", "user", "system"), required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, required=True)
    return parser.parse_args(argv)


def run(command: Sequence[object], *, repo_root: Path) -> None:
    """Run one installer command from the repository root."""

    printable = " ".join(str(part) for part in command)
    print(f"[install-project] RUN: {printable}")
    subprocess.run([str(part) for part in command], cwd=repo_root, check=True)


def install_package(venv_python: Path, mode: str, *, repo_root: Path) -> None:
    """Install Ubersight into the selected virtual environment."""

    if mode == "venv-only":
        return
    package_spec = ".[dev]" if mode == "dev" else "."
    command = [venv_python, "-m", "pip", "install", "--no-build-isolation"]
    if mode == "dev":
        command.append("--editable")
    command.append(package_spec)
    run(command, repo_root=repo_root)


def absolute_path_preserving_symlinks(path: Path) -> Path:
    """Return one absolute path without collapsing symlinked segments."""

    return Path(os.path.abspath(str(path.expanduser())))


def command_path(venv_path: Path, command_name: str) -> Path:
    """Return one console-script path inside a virtual environment."""

    bin_dir = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv_path / bin_dir / f"{command_name}{suffix}"


def ensure_launcher(link_path: Path, target_path: Path) -> None:
    """Create or update one symlink launcher without replacing real files."""

    if not target_path.is_file():
        raise RuntimeError(f"missing installed command: {target_path}")
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.is_symlink():
        current = link_path.resolve()
        if current == target_path.resolve():
            return
        link_path.unlink()
    elif link_path.exists():
        raise RuntimeError(f"refusing to replace non-symlink launcher: {link_path}")
    link_path.symlink_to(target_path)


def remove_owned_launcher(link_path: Path, venv_path: Path) -> None:
    """Remove a launcher symlink when it points inside the managed venv."""

    if not link_path.is_symlink():
        return
    try:
        target = link_path.resolve()
        target.relative_to(venv_path.resolve())
    except (OSError, ValueError):
        return
    link_path.unlink()


def venv_python(venv_path: Path) -> Path:
    """Return the Python executable inside one virtual environment."""

    bin_dir = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return venv_path / bin_dir / executable


def is_owned_copied_launcher(launcher: Path, venv_path: Path) -> bool:
    """Return whether a copied launcher runs this virtual environment.

    pip's launchers end with a `#!` line naming the environment's Python,
    quoted when the path contains spaces. Windows paths compare
    case-insensitively.
    """

    try:
        data = launcher.read_bytes().lower()
    except OSError:
        return False
    python = str(venv_python(venv_path)).lower().encode("utf-8")
    return b"#!" + python in data or b'#!"' + python + b'"' in data


def _is_owned_symlink(link_path: Path, venv_path: Path) -> bool:
    """Return whether a symlink launcher points inside the managed venv."""

    if not link_path.is_symlink():
        return False
    try:
        link_path.resolve().relative_to(venv_path.resolve())
    except (OSError, ValueError):
        return False
    return True


def retired_launcher_paths(launcher: Path) -> tuple[Path, ...]:
    """Return launcher copies that were renamed aside while still running."""

    return tuple(sorted(launcher.parent.glob(f"{launcher.name}.old-*")))


def _retire_launcher(launcher: Path) -> None:
    """Move a possibly running launcher aside and try to delete it.

    Windows cannot overwrite or delete a running executable, but it can
    rename one. The renamed copy is deleted now if it has exited, or by a
    later install otherwise.
    """

    retired = launcher.with_name(f"{launcher.name}.old-{os.getpid()}-{time.time_ns()}")
    os.replace(launcher, retired)
    with contextlib.suppress(OSError):
        retired.unlink()


def _remove_retired_launchers(launcher: Path) -> None:
    """Delete earlier retired copies that are no longer running."""

    for retired in retired_launcher_paths(launcher):
        with contextlib.suppress(OSError):
            retired.unlink()


def is_python_console_launcher(launcher: Path) -> bool:
    """Return whether a file is a pip console-script launcher for any Python.

    pip's launchers end with a `#!` line naming a Python interpreter,
    followed by a zip archive holding the entry point.
    """

    try:
        data = launcher.read_bytes()
    except OSError:
        return False
    marker = data.rfind(b"#!")
    if marker < 0:
        return False
    line_end = data.find(b"\n", marker)
    if line_end < 0:
        return False
    interpreter = data[marker + 2 : line_end].strip().strip(b'"').lower()
    is_python = interpreter.endswith((b"python.exe", b"pythonw.exe", b"python"))
    return is_python and b"PK" in data[line_end:]


def ensure_copied_launcher(launcher: Path, target_path: Path, venv_path: Path) -> None:
    """Create or refresh one copied launcher without replacing foreign files.

    An existing launcher is replaced when it is a Python console-script
    launcher, such as one from another Ubersight install mode, or an older
    symlink into this venv. This mirrors POSIX, where any launcher symlink is
    re-pointed. Any other file is refused. A launcher that is running, for
    example in an open dashboard pane, is renamed aside rather than failing
    the install.
    """

    if not target_path.is_file():
        raise RuntimeError(f"missing installed command: {target_path}")
    launcher.parent.mkdir(parents=True, exist_ok=True)
    _remove_retired_launchers(launcher)
    exists = launcher.exists() or launcher.is_symlink()
    if exists:
        replaceable = (
            is_owned_copied_launcher(launcher, venv_path)
            or _is_owned_symlink(launcher, venv_path)
            or (not launcher.is_symlink() and is_python_console_launcher(launcher))
        )
        if not replaceable:
            raise RuntimeError(f"refusing to replace unmanaged launcher: {launcher}")
        if (
            not launcher.is_symlink()
            and launcher.read_bytes() == target_path.read_bytes()
        ):
            return
    staged = launcher.with_name(f".{launcher.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(target_path, staged)
        try:
            os.replace(staged, launcher)
        except PermissionError:
            if not exists:
                raise
            _retire_launcher(launcher)
            os.replace(staged, launcher)
    finally:
        with contextlib.suppress(OSError):
            staged.unlink(missing_ok=True)


def remove_owned_copied_launcher(launcher: Path, venv_path: Path) -> None:
    """Remove a copied launcher owned by the managed venv, even if running."""

    _remove_retired_launchers(launcher)
    if not (
        is_owned_copied_launcher(launcher, venv_path)
        or _is_owned_symlink(launcher, venv_path)
    ):
        return
    try:
        launcher.unlink()
    except PermissionError:
        _retire_launcher(launcher)


def manage_launchers(
    *,
    mode: str,
    venv_path: Path,
    bin_dir: Path,
    copy_launchers: bool | None = None,
) -> tuple[Path, ...]:
    """Install or remove Ubersight command launchers.

    Windows copies the environment's `.exe` launchers by default; POSIX
    links to them.
    """

    if copy_launchers is None:
        copy_launchers = os.name == "nt"
    changed: list[Path] = []
    for command_name in COMMANDS:
        target_path = command_path(venv_path, command_name)
        if copy_launchers:
            launcher = bin_dir / target_path.name
            if mode == "venv-only":
                remove_owned_copied_launcher(launcher, venv_path)
                continue
            ensure_copied_launcher(launcher, target_path, venv_path)
            changed.append(launcher)
            continue
        link_path = bin_dir / command_name
        if mode == "venv-only":
            remove_owned_launcher(link_path, venv_path)
            continue
        ensure_launcher(link_path, target_path)
        changed.append(link_path)
    return tuple(changed)


def main(argv: Sequence[str] | None = None) -> int:
    """Install Ubersight according to TheKnowledge's stage-two contract."""

    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    venv_python = absolute_path_preserving_symlinks(Path(args.python))
    venv_path = args.venv.expanduser().resolve()
    bin_dir = args.bin_dir.expanduser().resolve()
    try:
        install_package(venv_python, args.mode, repo_root=repo_root)
        launchers = manage_launchers(
            mode=args.mode,
            venv_path=venv_path,
            bin_dir=bin_dir,
        )
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f"[install-project] FAIL: {error}", file=sys.stderr)
        return 1

    print("[install-project] PASS")
    print(f"Repository: {repo_root}")
    print(f"Mode: {args.mode}")
    print(f"Scope: {args.scope}")
    print(f"Virtualenv: {venv_path}")
    if args.mode == "venv-only":
        print("Launchers: owned launchers removed when present.")
    else:
        for launcher in launchers:
            print(f"Launcher: {launcher}")
    return 0


__all__ = [
    "COMMANDS",
    "absolute_path_preserving_symlinks",
    "command_path",
    "ensure_copied_launcher",
    "ensure_launcher",
    "install_package",
    "is_owned_copied_launcher",
    "is_python_console_launcher",
    "main",
    "manage_launchers",
    "parse_args",
    "remove_owned_copied_launcher",
    "remove_owned_launcher",
    "retired_launcher_paths",
    "venv_python",
]
