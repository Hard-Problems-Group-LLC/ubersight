#!/usr/bin/env python3
"""Install or bootstrap from a managed checkout after Python 3.9+ exists."""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from typing import Dict, Optional, Sequence, Tuple
from urllib.request import urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ubersight.install_scope import (  # noqa: E402
    default_pyenv_root as default_install_pyenv_root,
    default_tool_venv,
    default_user_bin_dir,
    describe_user_home_source,
    resolve_user_home,
)

from python_environment_bootstrap import (  # noqa: E402
    DIRENV_BEGIN,
    DIRENV_END,
    PYENV_INIT_BEGIN,
    PYENV_INIT_END,
    build_direnv_hook_block,
    build_envrc_content,
    detect_shell_name,
    direnv_download_name,
    ensure_minimum_python,
    ensure_pyenv_context,
    ensure_pyenv_installed,
    load_python_environment_config,
    pyenv_python_executable,
    pyenv_shell_init_snippet,
    shell_rc_path,
    slugify_project_name,
    upsert_managed_block,
    write_python_version_file,
)

STAGE1_MARKER = "THEKNOWLEDGE_MANAGED_INSTALL_STAGE1"
REPO_SCOPE = "repo"
USER_SCOPE = "user"
SYSTEM_SCOPE = "system"
DIRENV_DOWNLOAD_TIMEOUT_SECONDS = 30


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse stage-two installer arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Install from this checkout after a Python 3.9+ interpreter is "
            "available. Standard mode defaults to a user-local non-development "
            "install. Development mode provisions the managed repo-local "
            "toolchain."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("standard", "dev", "venv-only"),
        default="standard",
        help="Install mode. Default: standard",
    )
    parser.add_argument(
        "--system",
        action="store_true",
        help=(
            "Install standard mode into system locations. Requires root and "
            "cannot be combined with development or venv-only mode."
        ),
    )
    parser.add_argument(
        "--user-home",
        type=Path,
        default=None,
        help=(
            "Explicit target home for user-scoped pyenv, direnv, shell-init, "
            "and launcher paths. Required when the current HOME is an "
            "isolated repo-local Codex home and you intend to target another "
            "user scope."
        ),
    )
    parser.add_argument(
        "--allow-isolated-home",
        action="store_true",
        help=(
            "Allow user-scoped bootstrap state to target the current "
            "repo-local Codex home when HOME isolation is active."
        ),
    )
    parser.add_argument(
        "--skip-direnv-install",
        action="store_true",
        help="Fail instead of auto-installing direnv when dev mode needs it.",
    )
    parser.add_argument(
        "--skip-shell-init-update",
        action="store_true",
        help="Do not write managed pyenv or direnv shell-init blocks.",
    )
    parser.add_argument(
        "--skip-submodule-init",
        action="store_true",
        help="Skip `git submodule update --init --recursive`.",
    )
    parser.add_argument(
        "--force-direct-run",
        action="store_true",
        help="Bypass the stage-1 guard for explicit debugging.",
    )
    return parser.parse_args(argv)


def ensure_started_by_stage_1(force_direct_run: bool) -> None:
    """Reject direct execution unless explicitly allowed."""

    if force_direct_run:
        return
    if os.environ.get(STAGE1_MARKER) == "1":
        return
    raise RuntimeError(
        "scripts/install-stage-2.py must be started by ./install.sh. "
        "Use ./install.sh, or rerun with --force-direct-run when you are "
        "intentionally bypassing stage 1."
    )


def install_scope(args: argparse.Namespace) -> str:
    """Return the effective install scope for the selected mode."""

    if args.system:
        if args.user_home is not None or args.allow_isolated_home:
            raise RuntimeError(
                "--user-home and --allow-isolated-home are not valid with " "--system."
            )
        if args.mode != "standard":
            raise RuntimeError("--system is only valid with --mode standard.")
        if os.geteuid() != 0:
            raise RuntimeError("--system requires root privileges.")
        return SYSTEM_SCOPE
    if args.mode in ("dev", "venv-only"):
        return REPO_SCOPE
    return USER_SCOPE


def run(
    command: Sequence[str],
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """Run one external command with consistent settings."""

    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        env=env,
        check=True,
        capture_output=capture_output,
        text=True,
    )


def ensure_submodules(skip: bool) -> None:
    """Initialize submodules when the repository uses them."""

    if skip:
        return
    if not (REPO_ROOT / ".gitmodules").is_file():
        return
    run(["git", "submodule", "update", "--init", "--recursive"], cwd=REPO_ROOT)


def ensure_runtime_contexts(user_home: Path) -> Tuple[Path, str, Path]:
    """Install the configured bootstrap and runtime pyenv contexts."""

    config = load_python_environment_config(REPO_ROOT)
    ensure_minimum_python(
        sys.version_info[:3],
        config.bootstrap.required_version,
        "install-stage-2.py",
    )
    pyenv_root_path = default_install_pyenv_root(user_home)
    ensure_pyenv_installed(pyenv_root_path, run)
    runtime_selection = ensure_pyenv_context(pyenv_root_path, config.runtime, run)
    runtime_python = pyenv_python_executable(pyenv_root_path, runtime_selection)
    if not runtime_python.is_file():
        raise RuntimeError(
            "Expected runtime interpreter is missing: {}".format(runtime_python)
        )
    write_python_version_file(REPO_ROOT, runtime_selection)
    return pyenv_root_path, runtime_selection, runtime_python


def ensure_repo_venv(runtime_python: Path) -> Path:
    """Create or refresh `.venv` using the configured runtime interpreter."""

    run(
        [
            str(runtime_python),
            str(REPO_ROOT / "scripts" / "dev_setup.py"),
            "--python",
            str(runtime_python),
        ],
        cwd=REPO_ROOT,
    )
    return REPO_ROOT / ".venv" / "bin" / "python"


def venv_python_path(venv_path: Path) -> Path:
    """Return the Python path inside one virtual environment."""

    bin_dir = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return venv_path / bin_dir / executable


def interpreter_base_identity(python_executable: Path) -> Path:
    """Return the canonical base interpreter used by one Python executable."""

    completed = run(
        [
            str(python_executable),
            "-c",
            (
                "import os, sys; "
                "print(os.path.realpath(getattr(sys, '_base_executable', "
                "sys.executable)))"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    identity = completed.stdout.strip()
    if not identity:
        raise RuntimeError(
            "Python did not report its base interpreter: {}".format(python_executable)
        )
    return Path(identity)


def ensure_virtualenv(base_python: Path, venv_path: Path) -> Path:
    """Create a virtual environment or rebuild it after base-runtime drift."""

    venv_python = venv_python_path(venv_path)
    if venv_python.is_file():
        try:
            if interpreter_base_identity(venv_python) == interpreter_base_identity(
                base_python
            ):
                return venv_python
        except (OSError, RuntimeError, subprocess.CalledProcessError):
            pass
        run(
            [str(base_python), "-m", "venv", "--clear", str(venv_path)],
            cwd=REPO_ROOT,
        )
        return venv_python
    venv_path.parent.mkdir(parents=True, exist_ok=True)
    run([str(base_python), "-m", "venv", str(venv_path)], cwd=REPO_ROOT)
    return venv_python


def install_build_bootstrap(venv_python: Path) -> None:
    """Install the minimal build requirements for local package installs."""

    run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "setuptools>=69",
            "wheel",
        ],
        cwd=REPO_ROOT,
    )


def project_slug() -> str:
    """Return a stable slug for user or system install paths."""

    return slugify_project_name(REPO_ROOT.name)


def standard_install_venv_path(scope: str, user_home: Optional[Path] = None) -> Path:
    """Return the managed venv path for one non-development install scope."""

    if scope == SYSTEM_SCOPE:
        return Path("/usr/local/share") / project_slug() / "venv"
    if scope == USER_SCOPE:
        if user_home is None:
            raise ValueError("User-scoped installs require an explicit user home.")
        return default_tool_venv(user_home, project_slug())
    raise ValueError("Unsupported standard install scope: {}".format(scope))


def preferred_standard_base_python(
    scope: str,
    user_home: Optional[Path] = None,
) -> Path:
    """Choose the base interpreter for one standard non-development install."""

    if scope == SYSTEM_SCOPE:
        return Path(sys.executable).resolve()

    try:
        config = load_python_environment_config(REPO_ROOT)
    except (OSError, ValueError):
        return Path(sys.executable).resolve()

    candidate = pyenv_python_executable(
        default_install_pyenv_root(user_home),
        config.runtime.environment_name,
    )
    if candidate.is_file():
        return candidate
    return Path(sys.executable).resolve()


def ensure_standard_install_venv(
    scope: str,
    user_home: Optional[Path] = None,
) -> Tuple[Path, Path, Path]:
    """Create or refresh the user or system venv used for standard installs."""

    venv_path = standard_install_venv_path(scope, user_home)
    base_python = preferred_standard_base_python(scope, user_home)
    venv_python = ensure_virtualenv(base_python, venv_path)
    install_build_bootstrap(venv_python)
    return venv_path, venv_python, base_python


def _pyproject_text(repo_root: Path) -> str:
    path = repo_root / "pyproject.toml"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def has_dev_extra(repo_root: Path) -> bool:
    """Return True when `pyproject.toml` declares a `dev` extra."""

    text = _pyproject_text(repo_root)
    if not text:
        return False
    return bool(re.search(r"(?m)^dev\s*=\s*\[", text))


def launcher_dir_for_scope(scope: str, user_home: Optional[Path] = None) -> Path:
    """Return the managed launcher directory for one install scope."""

    if scope == SYSTEM_SCOPE:
        return Path("/usr/local/bin")
    if user_home is None:
        raise ValueError("User-scoped launchers require an explicit user home.")
    return default_user_bin_dir(user_home)


def run_project_install_hook(
    venv_python: Path,
    mode: str,
    scope: str,
    venv_path: Path,
    bin_dir: Path,
) -> bool:
    """Run an optional project install hook when the repository provides one."""

    hook = REPO_ROOT / "scripts" / "install_project.py"
    if not hook.is_file():
        return False
    run(
        [
            str(venv_python),
            str(hook),
            "--mode",
            mode,
            "--scope",
            scope,
            "--python",
            str(venv_python),
            "--venv",
            str(venv_path),
            "--bin-dir",
            str(bin_dir),
        ],
        cwd=REPO_ROOT,
    )
    return True


def default_install_command(venv_python: Path, mode: str) -> Sequence[str]:
    """Build the default pip install command for one managed install mode."""

    command = [str(venv_python), "-m", "pip", "install", "--no-build-isolation"]
    if mode == "dev":
        command.append("--editable")
        command.append(".[dev]" if has_dev_extra(REPO_ROOT) else ".")
        return command
    if mode == "standard":
        command.append(".")
        return command
    raise ValueError("No default install command for mode {}".format(mode))


def install_project(
    venv_python: Path,
    venv_path: Path,
    mode: str,
    scope: str,
    bin_dir: Path,
) -> None:
    """Install the repository according to the selected mode and scope."""

    if mode == "venv-only":
        run_project_install_hook(venv_python, mode, scope, venv_path, bin_dir)
        return
    if run_project_install_hook(venv_python, mode, scope, venv_path, bin_dir):
        return
    run(default_install_command(venv_python, mode), cwd=REPO_ROOT)


def install_git_hooks(venv_python: Path) -> None:
    """Install managed git hooks when the repository exposes an installer."""

    candidates = (
        REPO_ROOT / "scripts" / "install_git_hooks.py",
        REPO_ROOT / "TheKnowledge" / "scripts" / "install_git_hooks.py",
    )
    for installer in candidates:
        if installer.is_file():
            command = [str(venv_python), str(installer)]
            if installer.parent.parent.name == "TheKnowledge":
                command.extend(["--repo-root", str(REPO_ROOT)])
            run(command, cwd=REPO_ROOT)
            return


def ensure_direnv(auto_install: bool, user_home: Path) -> Path:
    """Locate or install a user-scoped `direnv` binary."""

    existing = shutil.which("direnv")
    if existing:
        return Path(existing)

    local_bin_dir = default_user_bin_dir(user_home)
    local_direnv = local_bin_dir / "direnv"
    if local_direnv.exists():
        return local_direnv

    if not auto_install:
        raise RuntimeError("direnv is required for development installs")

    local_bin_dir.mkdir(parents=True, exist_ok=True)
    artifact_name = direnv_download_name(platform.system().lower(), platform.machine())
    url = "https://github.com/direnv/direnv/releases/latest/download/{}".format(
        artifact_name
    )
    try:
        with urlopen(url, timeout=DIRENV_DOWNLOAD_TIMEOUT_SECONDS) as response:
            local_direnv.write_bytes(response.read())
    except OSError as error:
        raise RuntimeError(
            "Failed to download direnv from {} within {} seconds: {}".format(
                url,
                DIRENV_DOWNLOAD_TIMEOUT_SECONDS,
                error,
            )
        ) from error
    local_direnv.chmod(
        local_direnv.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    return local_direnv


def ensure_shell_init(mode: str, user_home: Path) -> Optional[Path]:
    """Install managed pyenv and optional direnv shell-hook blocks."""

    shell_name = detect_shell_name(os.environ.get("SHELL", ""))
    rc_path = shell_rc_path(user_home, shell_name)
    if rc_path is None:
        return None

    existing = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
    pyenv_snippet = pyenv_shell_init_snippet()
    updated = upsert_managed_block(
        existing,
        PYENV_INIT_BEGIN,
        PYENV_INIT_END,
        pyenv_snippet,
    )
    if mode == "dev":
        updated = upsert_managed_block(
            updated,
            DIRENV_BEGIN,
            DIRENV_END,
            build_direnv_hook_block(shell_name),
        )
    rc_path.write_text(updated, encoding="utf-8")
    return rc_path


def write_envrc() -> Path:
    """Write the managed repository `.envrc` file."""

    envrc_path = REPO_ROOT / ".envrc"
    envrc_path.write_text(build_envrc_content(), encoding="utf-8")
    return envrc_path


def allow_direnv(direnv_path: Path, envrc_path: Path) -> None:
    """Allow the repository direnv policy using the chosen binary."""

    env = os.environ.copy()
    env["PATH"] = str(direnv_path.parent) + os.pathsep + env.get("PATH", "")
    run([str(direnv_path), "allow", str(envrc_path.parent)], cwd=REPO_ROOT, env=env)


def verify_install(venv_python: Path) -> None:
    """Run minimal post-bootstrap verification."""

    run([str(venv_python), "--version"], cwd=REPO_ROOT)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Execute the managed stage-two install or bootstrap flow."""

    args = parse_args(argv or sys.argv[1:])
    pyenv_root_path = None
    runtime_selection = None
    selected_base_python = None
    install_venv = None
    target_user_home = None
    direnv_path = None
    envrc_path = None
    try:
        ensure_started_by_stage_1(args.force_direct_run)
        scope = install_scope(args)
        if scope != SYSTEM_SCOPE:
            target_user_home = resolve_user_home(
                REPO_ROOT,
                args.user_home,
                allow_isolated_home=args.allow_isolated_home,
            )
        launcher_bin_dir = launcher_dir_for_scope(scope, target_user_home)
        ensure_submodules(args.skip_submodule_init)
        if scope == REPO_SCOPE:
            pyenv_root_path, runtime_selection, runtime_python = (
                ensure_runtime_contexts(target_user_home)
            )
            venv_python = ensure_repo_venv(runtime_python)
            install_venv = REPO_ROOT / ".venv"
            install_project(
                venv_python,
                install_venv,
                args.mode,
                scope,
                launcher_bin_dir,
            )
            install_git_hooks(venv_python)
            if not args.skip_shell_init_update:
                ensure_shell_init(args.mode, target_user_home)
            if args.mode == "dev":
                direnv_path = ensure_direnv(
                    auto_install=not args.skip_direnv_install,
                    user_home=target_user_home,
                )
                envrc_path = write_envrc()
                allow_direnv(direnv_path, envrc_path)
        else:
            install_venv, venv_python, selected_base_python = (
                ensure_standard_install_venv(scope, target_user_home)
            )
            install_project(
                venv_python,
                install_venv,
                args.mode,
                scope,
                launcher_bin_dir,
            )
        verify_install(venv_python)
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print("[install-stage-2] FAIL: {}".format(error), file=sys.stderr)
        return 1

    print("[install-stage-2] PASS")
    print("Mode: {}".format(args.mode))
    print("Install scope: {}".format(scope))
    if target_user_home is not None:
        print("User home: {}".format(target_user_home))
        print(
            "User home source: {}".format(
                describe_user_home_source(REPO_ROOT, target_user_home, args.user_home)
            )
        )
    if pyenv_root_path is not None:
        print("Pyenv root: {}".format(pyenv_root_path))
    if runtime_selection is not None:
        print("Runtime selection: {}".format(runtime_selection))
    if selected_base_python is not None:
        print("Base Python: {}".format(selected_base_python))
    if install_venv is not None:
        print("Install venv: {}".format(install_venv))
    if direnv_path is not None:
        print("Direnv: {}".format(direnv_path))
    if envrc_path is not None:
        print("Envrc: {}".format(envrc_path))
    if args.mode == "dev":
        print("Next step: open a new shell in this repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
