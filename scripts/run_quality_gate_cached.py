#!/usr/bin/env python3
"""Run repository quality checks with content-hash caching."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

try:
    from tool_execution_constraints import (
        load_execution_constraints,
        serial_execution_constraint_label,
    )
except ImportError:  # pragma: no cover - import path varies by entry point.
    from scripts.tool_execution_constraints import (
        load_execution_constraints,
        serial_execution_constraint_label,
    )


SCHEMA_VERSION = "1.0.0"
DEFAULT_EXECUTION_CONSTRAINTS_FILE = "tool_execution_constraints.json"
ENSURE_TOOL_RUNTIME_SCRIPT = (
    Path(__file__).resolve().with_name("ensure_theknowledge_tool_runtime.py")
)
TIMEOUT_WRAPPER_SCRIPT = Path(__file__).resolve().with_name("run_tool_with_timeout.py")
NON_GIT_CACHE_DIR = ".cache"

DEFAULT_EXCLUDES = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "project.egg-info",
    "__pycache__",
}

CHECK_ORDER = [
    "black",
    "ruff",
    "compileall",
    "entropy_check",
    "entropy_tripwire_verify",
    "knack_check",
    "pytest",
]

CHECK_SCOPE: Dict[str, Dict[str, object]] = {
    "black": {
        "roots": [
            "src",
            "tests",
            "scripts",
            "standards-and-practices/dev-utils",
            "templates/scripts",
        ],
        "extensions": [".py", ".pyi"],
        "extra_files": [
            "pyproject.toml",
            "tool_validation_profiles.json",
            "scripts/tool_validation_profiles.py",
        ],
        "exclude_patterns": [],
    },
    "ruff": {
        "roots": [
            "src",
            "tests",
            "scripts",
            "standards-and-practices/dev-utils",
            "templates/scripts",
        ],
        "extensions": [".py", ".pyi"],
        "extra_files": ["pyproject.toml"],
        "exclude_patterns": [],
    },
    "compileall": {
        "roots": ["src", "tests"],
        "extensions": [".py"],
        "extra_files": [],
        "exclude_patterns": [],
    },
    "entropy_check": {
        "roots": ["."],
        "extensions": None,
        "extra_files": [],
        "exclude_patterns": ["knacks"],
        "skip_gitignored": True,
    },
    "entropy_tripwire_verify": {
        "roots": ["."],
        "extensions": None,
        "extra_files": [],
        "exclude_patterns": ["knacks"],
        "skip_gitignored": True,
    },
    "knack_check": {
        "roots": ["knacks"],
        "extensions": [".md"],
        "extra_files": [],
        "exclude_patterns": [],
    },
    "pytest": {
        "roots": [
            "src",
            "tests",
            "resources",
            "scripts",
            "standards-and-practices/dev-utils",
        ],
        "extensions": None,
        "extra_files": ["pyproject.toml"],
        "exclude_patterns": [],
    },
}


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Run standard quality checks using per-check content hash cache.")
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root (default: current directory).",
    )
    parser.add_argument(
        "--cache-file",
        default=".git/project-quality-cache.json",
        help="Cache file path relative to repo root.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cache and rerun all checks.",
    )
    parser.add_argument(
        "--checks",
        nargs="*",
        default=CHECK_ORDER,
        help="Subset of checks to run (default: full ordered set).",
    )
    parser.add_argument(
        "--show-cache",
        action="store_true",
        help="Print cache JSON and exit.",
    )
    return parser.parse_args(argv)


def path_is_excluded(path: Path, patterns: Sequence[str]) -> bool:
    as_posix = path.as_posix()
    parts = set(path.parts)
    for pattern in patterns:
        normalized = pattern.lstrip("./")
        if pattern in parts:
            return True
        if normalized and as_posix.endswith(normalized):
            return True
        if fnmatch.fnmatch(as_posix, pattern):
            return True
    return False


def iter_scope_files(
    repo_root: Path,
    roots: Sequence[str],
    extensions: Sequence[str] | None,
    extra_files: Sequence[str],
    exclude_patterns: Sequence[str] | None,
    *,
    skip_gitignored: bool = False,
) -> List[Path]:
    files: List[Path] = []
    excludes = list(DEFAULT_EXCLUDES) + list(exclude_patterns or [])
    extension_set = set(extensions) if extensions is not None else None

    for root_name in roots:
        root = (repo_root / root_name).resolve()
        if not root.exists():
            continue
        if root.is_file():
            rel = root.relative_to(repo_root)
            if not path_is_excluded(rel, excludes):
                if extension_set is None or rel.suffix.lower() in extension_set:
                    files.append(root)
            continue

        for current_root, dirnames, filenames in os.walk(root):
            current_path = Path(current_root)
            dirnames[:] = [
                name
                for name in dirnames
                if not path_is_excluded(
                    (current_path / name).relative_to(repo_root), excludes
                )
            ]
            for filename in filenames:
                candidate = current_path / filename
                rel = candidate.relative_to(repo_root)
                if path_is_excluded(rel, excludes):
                    continue
                if (
                    extension_set is not None
                    and rel.suffix.lower() not in extension_set
                ):
                    continue
                files.append(candidate)

    for extra in extra_files:
        extra_path = (repo_root / extra).resolve()
        if extra_path.exists() and extra_path.is_file():
            files.append(extra_path)

    unique_files = sorted(set(files))
    if not skip_gitignored:
        return unique_files

    ignored = git_ignored_paths(repo_root, unique_files)
    return [candidate for candidate in unique_files if candidate not in ignored]


def git_ignored_paths(repo_root: Path, candidates: Sequence[Path]) -> set[Path]:
    rel_paths = []
    for candidate in candidates:
        try:
            rel_paths.append(candidate.relative_to(repo_root).as_posix())
        except ValueError:
            continue
    if not rel_paths:
        return set()

    input_bytes = b"".join(relative.encode("utf-8") + b"\0" for relative in rel_paths)
    result = subprocess.run(
        ["git", "check-ignore", "--stdin", "-z"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        input=input_bytes,
    )
    if result.returncode not in {0, 1}:
        return set()

    ignored: set[Path] = set()
    for relative in result.stdout.decode("utf-8").split("\0"):
        if relative:
            ignored.add(repo_root / relative)
    return ignored


def fingerprint_files(
    repo_root: Path,
    files: Iterable[Path],
    check_name: str,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    digest.update(f"schema:{SCHEMA_VERSION}\ncheck:{check_name}\n".encode("utf-8"))
    count = 0
    for file_path in files:
        rel = file_path.relative_to(repo_root).as_posix()
        try:
            content = file_path.read_bytes()
        except FileNotFoundError:
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
        count += 1
    return digest.hexdigest(), count


def load_cache(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "checks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": SCHEMA_VERSION, "checks": {}}
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "checks": {}}
    if data.get("schema_version") != SCHEMA_VERSION:
        return {"schema_version": SCHEMA_VERSION, "checks": {}}
    checks = data.get("checks")
    if not isinstance(checks, dict):
        data["checks"] = {}
    return data


def resolve_git_dir(repo_root: Path) -> Path | None:
    if not repo_root.exists():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    git_dir_text = result.stdout.strip()
    if result.returncode != 0 or not git_dir_text:
        return None
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = (repo_root / git_dir).resolve()
    return git_dir


def cache_target_is_writable(path: Path) -> bool:
    candidate = path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return os.access(candidate, os.W_OK)


def resolve_cache_path(repo_root: Path, cache_file: str) -> Path:
    configured_path = Path(cache_file)
    if configured_path.is_absolute():
        return configured_path

    if configured_path.parts and configured_path.parts[0] == ".git":
        git_dir = resolve_git_dir(repo_root)
        if git_dir is not None:
            if len(configured_path.parts) == 1:
                git_path = git_dir
            else:
                git_path = git_dir.joinpath(*configured_path.parts[1:])
            if cache_target_is_writable(git_path):
                return git_path
        fallback_parts = list(configured_path.parts[1:]) or [
            "project-quality-cache.json"
        ]
        return (repo_root / NON_GIT_CACHE_DIR / Path(*fallback_parts)).resolve()

    return (repo_root / configured_path).resolve()


def is_theknowledge_direct_checkout(repo_root: Path) -> bool:
    return (repo_root / "internal" / "overrides" / "README.txt").is_file()


def configure_direct_checkout_runtime(repo_root: Path) -> None:
    if not is_theknowledge_direct_checkout(repo_root):
        return
    if os.environ.get("THEKNOWLEDGE_PYTHON_TOOLS"):
        return
    result = subprocess.run(
        [sys.executable, str(ENSURE_TOOL_RUNTIME_SCRIPT), "--print-python"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            "failed to ensure the direct-checkout tool runtime: {}".format(message)
        )
    runtime_python = (result.stdout or "").strip()
    if not runtime_python:
        raise RuntimeError("tool-runtime helper did not print an interpreter path")
    os.environ["THEKNOWLEDGE_PYTHON_TOOLS"] = runtime_python
    runtime_bin = str(Path(runtime_python).resolve().parent)
    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []
    if runtime_bin not in path_parts:
        os.environ["PATH"] = (
            runtime_bin + os.pathsep + current_path if current_path else runtime_bin
        )


def save_cache(path: Path, data: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {
        "schema_version": SCHEMA_VERSION,
        "checks": data.get("checks", {}),
    }
    path.write_text(
        json.dumps(ordered, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def run_check(
    repo_root: Path,
    check_name: str,
    paths: Sequence[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    repo_local_wrapper = repo_root / "scripts" / "run_tool_with_timeout.py"
    wrapper_script = (
        repo_local_wrapper if repo_local_wrapper.is_file() else TIMEOUT_WRAPPER_SCRIPT
    )
    command = [sys.executable, str(wrapper_script), check_name]
    if paths:
        command.extend(["--", *paths])
    return subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )


def execution_paths_for_check(
    repo_root: Path,
    files: Sequence[Path],
    extra_files: Sequence[str],
) -> List[str]:
    extra_file_set = set(extra_files)
    execution_paths: List[str] = []
    for file_path in files:
        relative_path = file_path.relative_to(repo_root).as_posix()
        if relative_path in extra_file_set:
            continue
        execution_paths.append(relative_path)
    return execution_paths


def run_serial_file_safe_check(
    repo_root: Path,
    check_name: str,
    paths: Sequence[str],
    constraint_label: str,
) -> subprocess.CompletedProcess[str]:
    stdout_chunks = [
        (
            f"[quality-gate] SERIAL {check_name}: matched "
            f"{constraint_label}; running {len(paths)} files one at a time."
        )
    ]
    stderr_chunks: List[str] = []

    for index, path in enumerate(paths, start=1):
        stdout_chunks.append(
            f"[quality-gate] SERIAL {check_name}: {index}/{len(paths)} {path}"
        )
        result = run_check(repo_root, check_name, paths=[path])
        if result.stdout:
            stdout_chunks.append(result.stdout.rstrip())
        if result.stderr:
            stderr_chunks.append(result.stderr.rstrip())
        if result.returncode != 0:
            stderr_chunks.append(
                f"[quality-gate] FAIL {check_name}: {path} exited "
                f"{result.returncode}."
            )
            return subprocess.CompletedProcess(
                ["serial-file-safe-check", check_name],
                result.returncode,
                "\n".join(stdout_chunks) + "\n",
                "\n".join(stderr_chunks) + "\n",
            )

    return subprocess.CompletedProcess(
        ["serial-file-safe-check", check_name],
        0,
        "\n".join(stdout_chunks) + "\n",
        "\n".join(stderr_chunks) + ("\n" if stderr_chunks else ""),
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()
    try:
        configure_direct_checkout_runtime(repo_root)
    except RuntimeError as error:
        print(f"[quality-gate] FAIL: {error}", file=sys.stderr)
        return 2
    cache_path = resolve_cache_path(repo_root, args.cache_file)
    constraints_path = repo_root / DEFAULT_EXECUTION_CONSTRAINTS_FILE

    try:
        execution_constraints = load_execution_constraints(constraints_path)
    except ValueError as error:
        print(f"[quality-gate] FAIL: {error}", file=sys.stderr)
        return 2

    cache = load_cache(cache_path)
    checks_cache = cache.setdefault("checks", {})
    if not isinstance(checks_cache, dict):
        checks_cache = {}
        cache["checks"] = checks_cache

    requested_checks = args.checks
    for check_name in requested_checks:
        if check_name not in CHECK_SCOPE:
            print(
                f"[quality-gate] FAIL: unknown check '{check_name}'.",
                file=sys.stderr,
            )
            return 2

    if args.show_cache:
        print(json.dumps(cache, indent=2, sort_keys=False))
        return 0

    for check_name in requested_checks:
        scope = CHECK_SCOPE[check_name]
        files = iter_scope_files(
            repo_root=repo_root,
            roots=scope["roots"],  # type: ignore[index]
            extensions=scope["extensions"],  # type: ignore[index]
            extra_files=scope["extra_files"],  # type: ignore[index]
            exclude_patterns=scope["exclude_patterns"],  # type: ignore[index]
            skip_gitignored=bool(scope.get("skip_gitignored", False)),
        )
        fingerprint, file_count = fingerprint_files(repo_root, files, check_name)

        cache_entry = checks_cache.get(check_name)
        if (
            not args.no_cache
            and isinstance(cache_entry, dict)
            and cache_entry.get("fingerprint") == fingerprint
            and cache_entry.get("status") == "pass"
        ):
            print(
                f"[quality-gate] SKIP {check_name}: cache hit "
                f"({file_count} files fingerprinted)."
            )
            continue

        print(
            f"[quality-gate] RUN {check_name}: cache miss "
            f"({file_count} files fingerprinted)."
        )
        execution_paths = execution_paths_for_check(
            repo_root,
            files,
            scope["extra_files"],  # type: ignore[index]
        )
        constraint_label = None
        if execution_paths:
            constraint_label = serial_execution_constraint_label(
                execution_constraints,
                check_name,
                len(execution_paths),
                "explicit_paths",
            )
        if constraint_label is not None:
            result = run_serial_file_safe_check(
                repo_root,
                check_name,
                execution_paths,
                constraint_label,
            )
        else:
            result = run_check(repo_root, check_name)
        output = (result.stdout or "") + (result.stderr or "")
        if output:
            print(output.rstrip())

        if result.returncode != 0:
            checks_cache[check_name] = {
                "status": "fail",
                "fingerprint": fingerprint,
                "files_count": file_count,
                "updated_at": now_iso(),
                "last_exit_code": result.returncode,
            }
            save_cache(cache_path, cache)
            print(
                f"[quality-gate] FAIL {check_name}: exit {result.returncode}.",
                file=sys.stderr,
            )
            return result.returncode

        checks_cache[check_name] = {
            "status": "pass",
            "fingerprint": fingerprint,
            "files_count": file_count,
            "updated_at": now_iso(),
            "last_exit_code": 0,
        }
        save_cache(cache_path, cache)

    print("[quality-gate] PASS: all requested checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
