"""Helpers for explicit user-home targeting across install entry points."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

CODEX_HOME_DIRNAME = ".codex-home"


def current_home() -> Path:
    """Return the current process home directory as a resolved path."""

    return Path.home().expanduser().resolve()


def default_user_bin_dir(user_home: Path) -> Path:
    """Return the managed user-local bin directory under one target home."""

    return user_home.expanduser().resolve() / ".local" / "bin"


def default_user_share_dir(user_home: Path) -> Path:
    """Return the managed user-local share directory under one target home."""

    return user_home.expanduser().resolve() / ".local" / "share"


def default_tool_venv(user_home: Path, tool_name: str) -> Path:
    """Return the default managed venv path for one tool under one home."""

    return default_user_share_dir(user_home) / tool_name / "venv"


def default_pyenv_root(user_home: Optional[Path] = None) -> Path:
    """Return the pyenv root for the selected target home."""

    if user_home is not None:
        return user_home.expanduser().resolve() / ".pyenv"

    configured = os.environ.get("PYENV_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return current_home() / ".pyenv"


def repo_local_codex_home(repo_root: Path) -> Path:
    """Return the expected repo-local Codex home path for one repository."""

    return (repo_root / CODEX_HOME_DIRNAME).resolve()


def is_repo_local_codex_home(user_home: Path, repo_root: Path) -> bool:
    """Return True when one home matches the repository's isolated Codex home."""

    return user_home.expanduser().resolve() == repo_local_codex_home(repo_root)


def describe_user_home_source(
    repo_root: Path,
    user_home: Path,
    explicit_home: Optional[Path],
) -> str:
    """Describe why one target user home was selected."""

    if explicit_home is not None:
        return "explicit --user-home target"
    if is_repo_local_codex_home(user_home, repo_root):
        return "current repo-local Codex home"
    return "current process HOME"


def resolve_user_home(
    repo_root: Path,
    explicit_home: Optional[Path] = None,
    *,
    allow_isolated_home: bool = False,
) -> Path:
    """Return the intended home for user-scoped installer side effects."""

    if explicit_home is not None:
        return explicit_home.expanduser().resolve()

    user_home = current_home()
    if is_repo_local_codex_home(user_home, repo_root) and not allow_isolated_home:
        raise RuntimeError(
            "Current HOME resolves to the repo-local Codex home ({}). Rerun "
            "from a normal terminal, pass --user-home PATH to target a "
            "different user scope explicitly, or pass --allow-isolated-home "
            "to install into the isolated AI home on purpose.".format(user_home)
        )
    return user_home


__all__ = [
    "CODEX_HOME_DIRNAME",
    "current_home",
    "default_pyenv_root",
    "default_tool_venv",
    "default_user_bin_dir",
    "default_user_share_dir",
    "describe_user_home_source",
    "is_repo_local_codex_home",
    "repo_local_codex_home",
    "resolve_user_home",
]
