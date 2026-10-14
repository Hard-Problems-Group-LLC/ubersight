"""Project install hook for Ubersight package and launcher installs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence

COMMANDS = (
    "ubersight",
    "ubersight-autosplit",
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


def manage_launchers(
    *,
    mode: str,
    venv_path: Path,
    bin_dir: Path,
) -> tuple[Path, ...]:
    """Install or remove Ubersight command launchers."""

    changed: list[Path] = []
    for command_name in COMMANDS:
        link_path = bin_dir / command_name
        if mode == "venv-only":
            remove_owned_launcher(link_path, venv_path)
            continue
        ensure_launcher(link_path, command_path(venv_path, command_name))
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
    "ensure_launcher",
    "install_package",
    "main",
    "manage_launchers",
    "parse_args",
    "remove_owned_launcher",
]
