#!/usr/bin/env python3
"""Install or bootstrap from a managed checkout after Python 3.9+ exists."""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.request import urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SRC_DIR = REPO_ROOT / "src"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
src_path = str(SRC_DIR)
if src_path in sys.path:
    sys.path.remove(src_path)
sys.path.insert(0, src_path)

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
    PythonContextConfig,
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
    rebuild_pyenv_context,
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
CURSES_IMPORT_COMMAND = "import curses"
CURSES_BUILD_PROBE_SOURCE = "#include <curses.h>\nint main(void) { return 0; }\n"
PYTHON_RUNTIME_PROBE = (
    "import sys; " "print('.'.join(str(part) for part in sys.version_info[:3]))"
)
WINDOWS_CURSES_DISTRIBUTION = "windows-curses"
# Matches the start of `dependencies = [` on its own line in `[project]`.
PROJECT_DEPENDENCIES_START = re.compile(r"(?m)^dependencies\s*=\s*\[")
# One token inside a TOML array of strings: a basic string, a literal
# string, a comment, or the closing bracket. Strings are matched before
# comments, so a `#` or `]` inside a requirement string is never misread.
TOML_ARRAY_TOKEN = re.compile(r'"((?:[^"\\\n]|\\.)*)"|\'([^\'\n]*)\'|#[^\n]*|(\])')


class CursesSupportError(RuntimeError):
    """Report that a selected Python runtime lacks Ubersight's curses support."""


class PythonRuntimeError(RuntimeError):
    """Report that a selected Python executable is missing or unusable."""


class VenvInUseError(RuntimeError):
    """Report that running programs hold a virtual environment open."""


def broken_pyenv_rebuild_required(
    python_executable: Path,
    error: PythonRuntimeError,
) -> RuntimeError:
    """Build the guarded-repair error shown for an unusable pyenv runtime."""

    return RuntimeError(
        "Detected an unusable configured pyenv runtime at {}: {}. The "
        "installer did not modify that runtime. Rerun the same install "
        "command with --rebuild-broken-pyenv to authorize a transactional "
        "rebuild.".format(python_executable, error)
    )


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
    parser.add_argument(
        "--no-modify-path",
        action="store_true",
        help=(
            "On Windows, do not add the launcher directory to the user PATH "
            "when it is missing; print instructions instead."
        ),
    )
    parser.add_argument(
        "--rebuild-broken-pyenv",
        action="store_true",
        help=(
            "Authorize replacement of a configured pyenv runtime that exists "
            "but fails its interpreter integrity probe. Without this flag the "
            "installer reports the damage and exits without rebuilding it."
        ),
    )
    return parser.parse_args(argv)


def ensure_started_by_stage_1(force_direct_run: bool) -> None:
    """Reject direct execution unless explicitly allowed."""

    if force_direct_run:
        return
    if os.environ.get(STAGE1_MARKER) == "1":
        return
    raise RuntimeError(
        "scripts/install-stage-2.py must be started by ./install.sh "
        "(install.ps1 on Windows). Use the stage-one installer, or rerun with "
        "--force-direct-run when you are intentionally bypassing stage 1."
    )


def is_windows() -> bool:
    """Return whether stage two is running on native Windows."""

    return os.name == "nt"


def install_scope(args: argparse.Namespace) -> str:
    """Return the effective install scope for the selected mode."""

    if args.system:
        if is_windows():
            raise RuntimeError(
                "--system is not supported on Windows. The default user "
                "install publishes launchers to ~\\.local\\bin instead."
            )
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


def ensure_runtime_contexts(
    user_home: Path,
    rebuild_broken_pyenv: bool = False,
) -> Tuple[Path, str, Path]:
    """Install the configured bootstrap and runtime pyenv contexts."""

    config = load_python_environment_config(REPO_ROOT)
    ensure_minimum_python(
        sys.version_info[:3],
        config.bootstrap.required_version,
        "install-stage-2.py",
    )
    pyenv_root_path = default_install_pyenv_root(user_home)
    ensure_pyenv_installed(pyenv_root_path, run)
    base_runtime_python = pyenv_python_executable(
        pyenv_root_path,
        config.runtime.base_version,
    )
    if not base_runtime_python.is_file():
        verify_curses_build_prerequisite()
    runtime_selection = ensure_pyenv_context(pyenv_root_path, config.runtime, run)
    runtime_python = pyenv_python_executable(pyenv_root_path, runtime_selection)
    try:
        verify_python_runtime(runtime_python, config.runtime.base_version)
    except PythonRuntimeError as error:
        if not rebuild_broken_pyenv:
            raise broken_pyenv_rebuild_required(runtime_python, error) from error
        verify_curses_build_prerequisite()
        print(
            "[install-stage-2] Rebuilding unusable pyenv runtime {} after "
            "explicit --rebuild-broken-pyenv authorization.".format(
                config.runtime.base_version
            ),
            flush=True,
        )
        runtime_selection = rebuild_runtime_context(
            pyenv_root_path,
            config.runtime,
        )
        runtime_python = pyenv_python_executable(pyenv_root_path, runtime_selection)
        write_python_version_file(REPO_ROOT, runtime_selection)
        return pyenv_root_path, runtime_selection, runtime_python

    try:
        verify_curses_support(runtime_python)
    except CursesSupportError:
        verify_curses_build_prerequisite()
        print(
            "[install-stage-2] Rebuilding pyenv runtime {} because it lacks "
            "curses support.".format(config.runtime.base_version),
            flush=True,
        )
        try:
            runtime_selection = rebuild_runtime_context(
                pyenv_root_path,
                config.runtime,
            )
        except (OSError, subprocess.CalledProcessError) as error:
            raise RuntimeError(
                "Automatic pyenv rebuild failed. Resolve the build error, then "
                "rerun the original ./install.sh command; the installer will "
                "preserve and validate the live prefix transactionally."
            ) from error
        runtime_python = pyenv_python_executable(pyenv_root_path, runtime_selection)
    write_python_version_file(REPO_ROOT, runtime_selection)
    return pyenv_root_path, runtime_selection, runtime_python


def rebuild_runtime_context(
    pyenv_root_path: Path,
    context: PythonContextConfig,
) -> str:
    """Rebuild and validate one configured runtime before discarding its backup."""

    def validate(candidate: Path) -> None:
        """Require both interpreter integrity and Ubersight terminal support."""

        verify_python_runtime(candidate, context.base_version)
        verify_curses_support(candidate)

    return rebuild_pyenv_context(
        pyenv_root_path,
        context,
        run,
        validator=validate,
    )


def ensure_repo_venv(runtime_python: Path) -> Path:
    """Create or refresh `.venv` using the configured runtime interpreter."""

    venv_path = REPO_ROOT / ".venv"
    # dev_setup.py is managed upstream and clears `.venv` without checking
    # whether it is in use, so the Windows check runs here first.
    if is_windows() and repo_venv_rebuild_pending(venv_path, runtime_python):
        ensure_venv_not_in_use(venv_path)
    run(
        [
            str(runtime_python),
            str(REPO_ROOT / "scripts" / "dev_setup.py"),
            "--python",
            str(runtime_python),
        ],
        cwd=REPO_ROOT,
    )
    return venv_python_path(venv_path)


def python_version_report(python_executable: Path) -> Optional[str]:
    """Return the `--version` text an interpreter prints, or None on failure."""

    try:
        completed = run(
            [str(python_executable), "--version"],
            cwd=REPO_ROOT,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    report = (completed.stdout or completed.stderr or "").strip()
    return report or None


def repo_venv_rebuild_pending(venv_path: Path, runtime_python: Path) -> bool:
    """Return whether `dev_setup.py` will clear and rebuild `venv_path`.

    This mirrors `ensure_virtualenv` in `dev_setup.py`: an existing
    environment is cleared when its interpreter reports a different version
    than the selected runtime. When either version probe fails, dev_setup.py
    fails the same probe before it deletes anything, so no rebuild is
    pending.
    """

    venv_python = venv_python_path(venv_path)
    if not venv_python.exists():
        return False
    current = python_version_report(venv_python)
    selected = python_version_report(runtime_python)
    if current is None or selected is None:
        return False
    return current != selected


def file_in_use(path: Path) -> bool:
    """Return whether Windows refuses write access to `path` because it runs.

    Windows lets a running executable be renamed, but not deleted or opened
    for writing. Opening it for writing, without writing anything, detects a
    running interpreter while leaving the file untouched.
    """

    try:
        with open(path, "r+b"):
            pass
    except FileNotFoundError:
        return False
    except PermissionError:
        return True
    return False


def ensure_venv_not_in_use(venv_path: Path) -> None:
    """Stop before a Windows venv rebuild would delete files that are in use.

    `venv --clear` deletes the environment's files in turn and fails at the
    first one Windows refuses to delete. A running dashboard keeps
    `Scripts\\python.exe` busy, so the clear would already have removed
    `Lib` and `pyvenv.cfg`, leaving a gutted environment that every launcher
    still points at. Checking the executables first stops the install while
    nothing has been deleted. POSIX hosts can delete files that are in use,
    so they are not checked.
    """

    if not is_windows():
        return
    busy = [
        path
        for path in sorted((venv_path / "Scripts").glob("*.exe"))
        if file_in_use(path)
    ]
    if busy:
        raise VenvInUseError(
            "Cannot rebuild {} for the selected Python: {} is in use by a "
            "running program, most likely an Ubersight dashboard. Nothing was "
            "changed. Close the running Ubersight dashboards (press Q in each "
            "pane), then rerun the installer.".format(venv_path, busy[0].name)
        )


def windows_repo_runtime_python() -> Path:
    """Return the interpreter that builds the repository venv on Windows.

    Windows has no pyenv source-build toolchain here. install.ps1 already
    chose the pinned runtime when the `py` launcher has it, or the newest
    Python otherwise. That interpreter must still meet the configured
    runtime floor, and a version other than the pinned one is reported.
    """

    config = load_python_environment_config(REPO_ROOT)
    ensure_minimum_python(
        sys.version_info[:3],
        config.runtime.required_version,
        "A Windows development install",
    )
    current = ".".join(str(part) for part in sys.version_info[:3])
    if current != config.runtime.base_version:
        print(
            "[install-stage-2] Using Python {} for the repository venv; the "
            "pinned runtime {} is not installed.".format(
                current, config.runtime.base_version
            ),
            flush=True,
        )
    return Path(sys.executable).resolve()


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
        ensure_venv_not_in_use(venv_path)
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
    rebuild_broken_pyenv: bool = False,
) -> Tuple[Path, Path, Path]:
    """Create or refresh the user or system venv used for standard installs."""

    venv_path = standard_install_venv_path(scope, user_home)
    base_python = preferred_standard_base_python(scope, user_home)
    if scope == USER_SCOPE:
        config = load_python_environment_config(REPO_ROOT)
        configured_python = pyenv_python_executable(
            default_install_pyenv_root(user_home),
            config.runtime.environment_name,
        )
        if base_python == configured_python:
            try:
                verify_python_runtime(base_python, config.runtime.base_version)
            except PythonRuntimeError as error:
                if not rebuild_broken_pyenv:
                    raise broken_pyenv_rebuild_required(base_python, error) from error
                _, _, base_python = ensure_runtime_contexts(
                    user_home,
                    rebuild_broken_pyenv=True,
                )
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


def project_dependencies(repo_root: Path) -> List[str]:
    """Return the `[project]` dependency strings from `pyproject.toml`.

    Stage two must run on Python 3.9, which has no `tomllib`, so this scans
    only the one array it needs: the `dependencies` key of the `[project]`
    table, written as an array of strings.
    """

    text = _pyproject_text(repo_root)
    table = re.search(r"(?ms)^\[project\][ \t]*$(.*?)(?=^\[|\Z)", text)
    if table is None:
        return []
    body = table.group(1)
    start = PROJECT_DEPENDENCIES_START.search(body)
    if start is None:
        return []
    dependencies = []
    for token in TOML_ARRAY_TOKEN.finditer(body, start.end()):
        if token.group(3):
            break
        value = token.group(1) if token.group(1) is not None else token.group(2)
        if value is not None:
            dependencies.append(value)
    return dependencies


def windows_curses_requirement(repo_root: Path) -> str:
    """Return the `windows-curses` requirement that `pyproject.toml` declares.

    Reading it keeps the version floor in one place. The environment marker
    is dropped because the caller installs it only on Windows.
    """

    for requirement in project_dependencies(repo_root):
        name = re.match(r"[A-Za-z0-9._-]+", requirement)
        if name and re.sub(r"[-_.]+", "-", name.group(0)).lower() == (
            WINDOWS_CURSES_DISTRIBUTION
        ):
            return requirement.split(";", 1)[0].strip()
    raise RuntimeError(
        "pyproject.toml does not declare the {} dependency that Ubersight "
        "needs on Windows.".format(WINDOWS_CURSES_DISTRIBUTION)
    )


def ensure_windows_venv_only_curses(venv_python: Path, mode: str) -> bool:
    """Install `windows-curses` into a Windows venv-only `.venv`.

    Venv-only mode builds `.venv` from the development tool requirements and
    skips the package install, so nothing else brings in the curses runtime
    that the dashboard and the tests need when they run from source. Return
    whether the install ran.
    """

    if mode != "venv-only" or not is_windows():
        return False
    run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            windows_curses_requirement(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
    )
    return True


def launcher_dir_for_scope(scope: str, user_home: Optional[Path] = None) -> Path:
    """Return the managed launcher directory for one install scope."""

    if scope == SYSTEM_SCOPE:
        return Path("/usr/local/bin")
    if user_home is None:
        raise ValueError("User-scoped launchers require an explicit user home.")
    return default_user_bin_dir(user_home)


def normalized_path_entry(entry: str) -> str:
    """Return one PATH entry normalized for comparison on this platform."""

    expanded = os.path.expandvars(os.path.expanduser(entry.strip().strip('"')))
    if not expanded:
        return ""
    return os.path.normcase(os.path.normpath(expanded))


def path_value_contains(
    path_value: str, directory: Path, separator: str = os.pathsep
) -> bool:
    """Return whether a PATH-style value already lists `directory`."""

    target = normalized_path_entry(str(directory))
    return any(
        normalized_path_entry(entry) == target
        for entry in path_value.split(separator)
        if entry.strip()
    )


def append_path_entry(
    path_value: str, directory: Path, separator: str = os.pathsep
) -> str:
    """Return a PATH-style value with `directory` appended once."""

    stripped = path_value.rstrip(separator)
    return (
        "{}{}{}".format(stripped, separator, directory) if stripped else str(directory)
    )


class WindowsUserPath:
    """Read and update the per-user PATH stored in `HKCU\\Environment`.

    The value keeps its registry type, so `REG_EXPAND_SZ` entries such as
    `%USERPROFILE%\\bin` continue to expand. After a change, running programs
    are told to reload the environment, so new terminals see it at once.
    """

    def read(self) -> Tuple[str, int]:
        """Return the user PATH value and its registry type."""

        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                value, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return "", winreg.REG_EXPAND_SZ
        return str(value), kind

    def write(self, value: str, kind: int) -> None:
        """Store a new user PATH value with the given registry type."""

        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "Path", 0, kind, value)

    def broadcast(self) -> None:
        """Notify running programs that environment settings changed."""

        import ctypes

        hwnd_broadcast = 0xFFFF
        wm_settingchange = 0x001A
        smto_abortifhung = 0x0002
        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_wchar_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        result = ctypes.c_size_t()
        send(
            hwnd_broadcast,
            wm_settingchange,
            0,
            "Environment",
            smto_abortifhung,
            5000,
            ctypes.byref(result),
        )


def ensure_launcher_dir_on_path(
    bin_dir: Path,
    *,
    modify: bool,
    user_path: Optional[WindowsUserPath] = None,
) -> str:
    """Make the launcher directory reachable from new terminals, or explain how.

    Windows appends a missing directory to the user PATH unless `modify` is
    false. It never adds a directory inside this checkout, such as an
    isolated AI home, so a sandboxed install cannot leak into the real
    environment. POSIX hosts only report a missing directory.
    """

    if not is_windows():
        if path_value_contains(os.environ.get("PATH", ""), bin_dir):
            return "Launcher directory is on PATH: {}".format(bin_dir)
        return (
            "Launcher directory is not on PATH: {0}. Add it in your shell "
            'profile, for example: export PATH="{0}:$PATH"'.format(bin_dir)
        )
    registry = user_path or WindowsUserPath()
    value, kind = registry.read()
    if path_value_contains(value, bin_dir, ";"):
        return "Launcher directory is on the user PATH: {}".format(bin_dir)
    resolved = bin_dir.expanduser().resolve()
    root = REPO_ROOT.resolve()
    if resolved == root or root in resolved.parents:
        return (
            "Launcher directory {} is inside this checkout; not adding it to "
            "the user PATH.".format(bin_dir)
        )
    if not modify:
        return (
            "Launcher directory is not on the user PATH: {}. Rerun without "
            "--no-modify-path, or add it in the Environment Variables dialog "
            "(rundll32.exe sysdm.cpl,EditEnvironmentVariables).".format(bin_dir)
        )
    registry.write(append_path_entry(value, bin_dir, ";"), kind)
    registry.broadcast()
    return (
        "Added {} to the user PATH; open a new terminal to use the "
        "commands.".format(bin_dir)
    )


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


MANAGED_GIT_HOOKS = ("pre-commit", "pre-push")


def normalize_git_hook_line_endings(
    hooks_dir: Optional[Path] = None,
) -> Tuple[Path, ...]:
    """Convert managed hook wrappers to LF line endings.

    The shared hook installer writes its bash wrappers in text mode, which
    produces CRLF on Windows. Git's bash then rejects `set -euo pipefail\\r`,
    so every commit would fail. Only the managed hook names are touched.
    """

    directory = hooks_dir or (REPO_ROOT / ".git" / "hooks")
    if not directory.is_dir():
        return ()
    converted = []
    for name in MANAGED_GIT_HOOKS:
        hook = directory / name
        try:
            data = hook.read_bytes()
        except OSError:
            continue
        if b"\r\n" in data:
            hook.write_bytes(data.replace(b"\r\n", b"\n"))
            converted.append(hook)
    return tuple(converted)


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


def _compact_failure_detail(stdout: str, stderr: str) -> str:
    """Return the final diagnostic line from one failed child process."""

    details = (stderr or stdout).strip()
    return details.splitlines()[-1] if details else ""


def _curses_build_probe_command() -> list[str]:
    """Build a no-output compiler probe for the native curses header."""

    compiler = shlex.split(os.environ.get("CC", "cc"))
    cppflags = shlex.split(os.environ.get("CPPFLAGS", ""))
    return [*compiler, *cppflags, "-x", "c", "-fsyntax-only", "-"]


def verify_curses_build_prerequisite() -> None:
    """Require usable curses headers before pyenv compiles a new runtime.

    CPython silently omits `_curses` when its headers are absent. A syntax-only
    compiler probe catches that condition before pyenv starts a lengthy source
    build, while honoring the caller's configured compiler and include flags.
    """

    command = _curses_build_probe_command()
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            input=CURSES_BUILD_PROBE_SOURCE,
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise RuntimeError(
            "Unable to run the compiler needed to verify curses headers: {}. "
            "Run ./scripts/install_prerequisites.sh to install the host build "
            "prerequisites, then rerun ./install.sh.".format(error)
        ) from error
    if result.returncode != 0:
        details = _compact_failure_detail(result.stdout, result.stderr)
        detail_suffix = ": {}".format(details) if details else ""
        raise RuntimeError(
            "Cannot compile a curses header probe{}; run "
            "./scripts/install_prerequisites.sh before pyenv builds the "
            "runtime. It installs ncurses development headers (Fedora/Rocky: "
            "`ncurses-devel`; Debian/Ubuntu: `libncursesw5-dev`; "
            "macOS/Homebrew: `ncurses`).".format(detail_suffix)
        )


def verify_curses_support(python_executable: Path) -> None:
    """Require the selected runtime to support Ubersight's terminal UI.

    A pyenv source build can complete without optional native extensions when
    its corresponding development headers are absent. Checking the actual
    interpreter keeps the installer from reporting success for a runtime that
    cannot start Ubersight's curses dashboard.
    """

    command = [str(python_executable), "-c", CURSES_IMPORT_COMMAND]
    try:
        run(command, cwd=REPO_ROOT, capture_output=True)
    except subprocess.CalledProcessError as error:
        details = _compact_failure_detail(error.stdout or "", error.stderr or "")
        detail_suffix = ": {}".format(details) if details else ""
        if is_windows():
            # Windows CPython ships without `_curses`; the windows-curses
            # wheel supplies it, and no native build step is involved.
            raise CursesSupportError(
                "Selected Python runtime cannot import curses{}; on Windows, "
                "Ubersight needs the windows-curses package in that "
                "environment. Rerun .\\install.ps1, which installs it in "
                "every mode, or install it directly with: "
                '"{}" -m pip install {}'.format(
                    detail_suffix, python_executable, WINDOWS_CURSES_DISTRIBUTION
                )
            ) from error
        raise CursesSupportError(
            "Selected Python runtime cannot import curses{}; Ubersight needs "
            "native terminal support. Run ./scripts/install_prerequisites.sh "
            "to install ncurses development headers (Fedora/Rocky: "
            "`ncurses-devel`; Debian/Ubuntu: `libncursesw5-dev`; "
            "macOS/Homebrew: `ncurses`), then rerun ./install.sh. The "
            "development installer will rebuild the selected pyenv runtime "
            "automatically.".format(detail_suffix)
        ) from error


def verify_python_runtime(
    python_executable: Path,
    expected_version: str,
) -> None:
    """Require a nonempty executable that reports the configured Python version."""

    try:
        file_size = python_executable.stat().st_size
    except OSError as error:
        raise PythonRuntimeError("interpreter file is missing or unreadable") from error
    if file_size == 0:
        raise PythonRuntimeError("interpreter file is empty")
    if not python_executable.is_file():
        raise PythonRuntimeError("interpreter path is not a regular file")
    if not os.access(python_executable, os.X_OK):
        raise PythonRuntimeError("interpreter file is not executable")

    command = [str(python_executable), "-c", PYTHON_RUNTIME_PROBE]
    try:
        completed = run(command, cwd=REPO_ROOT, capture_output=True)
    except OSError as error:
        raise PythonRuntimeError(
            "interpreter could not be executed: {}".format(error)
        ) from error
    except subprocess.CalledProcessError as error:
        details = _compact_failure_detail(error.stdout or "", error.stderr or "")
        detail_suffix = ": {}".format(details) if details else ""
        raise PythonRuntimeError(
            "interpreter probe failed{}".format(detail_suffix)
        ) from error

    reported_version = (completed.stdout or "").strip()
    if reported_version != expected_version:
        raise PythonRuntimeError(
            "interpreter reported Python {}, expected {}".format(
                reported_version or "an empty version",
                expected_version,
            )
        )


def verify_install(venv_python: Path) -> None:
    """Verify the installed environment and its required terminal support."""

    run([str(venv_python), "--version"], cwd=REPO_ROOT)
    verify_curses_support(venv_python)


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
    path_status = None
    notes = []
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
            if is_windows():
                runtime_python = windows_repo_runtime_python()
            else:
                pyenv_root_path, runtime_selection, runtime_python = (
                    ensure_runtime_contexts(
                        target_user_home,
                        rebuild_broken_pyenv=args.rebuild_broken_pyenv,
                    )
                )
            venv_python = ensure_repo_venv(runtime_python)
            ensure_windows_venv_only_curses(venv_python, args.mode)
            install_venv = REPO_ROOT / ".venv"
            install_project(
                venv_python,
                install_venv,
                args.mode,
                scope,
                launcher_bin_dir,
            )
            install_git_hooks(venv_python)
            if is_windows():
                normalize_git_hook_line_endings()
                # pyenv, direnv, and the bash/zsh shell-init blocks have no
                # native Windows counterpart in this installer.
                notes.append(
                    "Skipped pyenv, direnv, and shell-init setup on Windows. "
                    "Activate the venv with .\\.venv\\Scripts\\Activate.ps1."
                )
            elif not args.skip_shell_init_update:
                ensure_shell_init(args.mode, target_user_home)
            if args.mode == "dev" and not is_windows():
                direnv_path = ensure_direnv(
                    auto_install=not args.skip_direnv_install,
                    user_home=target_user_home,
                )
                envrc_path = write_envrc()
                allow_direnv(direnv_path, envrc_path)
        else:
            install_venv, venv_python, selected_base_python = (
                ensure_standard_install_venv(
                    scope,
                    target_user_home,
                    rebuild_broken_pyenv=args.rebuild_broken_pyenv,
                )
            )
            install_project(
                venv_python,
                install_venv,
                args.mode,
                scope,
                launcher_bin_dir,
            )
        verify_install(venv_python)
        if args.mode != "venv-only":
            path_status = ensure_launcher_dir_on_path(
                launcher_bin_dir, modify=not args.no_modify_path
            )
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
    if path_status is not None:
        print(path_status)
    for note in notes:
        print("Note: {}".format(note))
    if args.mode == "dev":
        print("Next step: open a new shell in this repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
