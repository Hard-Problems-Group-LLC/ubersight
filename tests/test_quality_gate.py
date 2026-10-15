"""Regression tests for the cached quality gate's file scope and cache key.

The gate fingerprints every in-scope file before deciding whether a check can
be skipped. These tests pin three safety properties: checkout-local `.local/`
state is never read; ignore checks keep working when a submodule is
populated, because `git check-ignore` rejects paths inside submodules; and a
cached pass never carries over to another platform or Python version.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Deliberate fixture identity for throwaway repositories created by tests.
FIXTURE_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Gate Fixture",
    "GIT_AUTHOR_EMAIL": "gate-fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Gate Fixture",
    "GIT_COMMITTER_EMAIL": "gate-fixture@example.invalid",
}


def load_quality_gate_module():
    """Load `scripts/run_quality_gate_cached.py` with its sibling imports."""

    module_path = ROOT / "scripts" / "run_quality_gate_cached.py"
    spec = importlib.util.spec_from_file_location("quality_gate_for_tests", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    original_path = list(sys.path)
    sys.path.insert(0, str(module_path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    return module


def git(cwd: Path, *args: str) -> None:
    """Run one fixture Git command, failing the test on error."""

    env = {**os.environ, **FIXTURE_GIT_ENV}
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def superproject(tmp_path: Path) -> Path:
    """Create a repository with a populated submodule and local state."""

    library = tmp_path / "library-src"
    library.mkdir()
    git(library, "init", "-q")
    (library / "tracked.txt").write_text("library\n", encoding="utf-8")
    (library / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    git(library, "add", ".")
    git(library, "commit", "-q", "-m", "library")

    project = tmp_path / "project"
    project.mkdir()
    git(project, "init", "-q")
    (project / ".gitignore").write_text(".local/\n", encoding="utf-8")
    (project / "app.py").write_text("print('app')\n", encoding="utf-8")
    git(project, "add", ".")
    git(project, "commit", "-q", "-m", "project")
    git(project, "submodule", "add", "-q", library.as_posix(), "lib")
    git(project, "commit", "-q", "-m", "add submodule")

    (project / "lib" / "scratch.tmp").write_text("ignored\n", encoding="utf-8")
    local_state = project / ".local" / "nested"
    local_state.mkdir(parents=True)
    (local_state / "secret.txt").write_text("local only\n", encoding="utf-8")
    return project.resolve()


def relative_names(project: Path, paths) -> set[str]:
    """Return `paths` as POSIX-style names relative to `project`."""

    return {path.relative_to(project).as_posix() for path in paths}


def test_ignore_check_handles_populated_submodules(superproject: Path) -> None:
    """Verify ignore checks cover the superproject and populated submodules."""

    gate = load_quality_gate_module()
    candidates = [
        superproject / "app.py",
        superproject / ".local" / "nested" / "secret.txt",
        superproject / "lib" / "tracked.txt",
        superproject / "lib" / "scratch.tmp",
    ]

    ignored = gate.git_ignored_paths(superproject, candidates)

    assert relative_names(superproject, ignored) == {
        ".local/nested/secret.txt",
        "lib/scratch.tmp",
    }


def test_scope_excludes_local_state_and_submodule_ignores(superproject: Path) -> None:
    """Verify the scan skips `.local/` and files a submodule ignores."""

    gate = load_quality_gate_module()

    files = gate.iter_scope_files(
        superproject,
        roots=["."],
        extensions=None,
        extra_files=[],
        exclude_patterns=[],
        skip_gitignored=True,
    )

    names = relative_names(superproject, files)
    assert "app.py" in names
    assert "lib/tracked.txt" in names
    assert "lib/scratch.tmp" not in names
    assert not any(name.startswith(".local/") for name in names)


def test_scope_never_walks_local_state_even_without_ignore_checks(
    superproject: Path,
) -> None:
    """Verify `.local/` stays out of scope even with ignore checks disabled."""

    gate = load_quality_gate_module()

    files = gate.iter_scope_files(
        superproject,
        roots=["."],
        extensions=None,
        extra_files=[],
        exclude_patterns=[],
        skip_gitignored=False,
    )

    assert not any(
        name.startswith(".local/") for name in relative_names(superproject, files)
    )


def test_ignore_check_fails_closed_outside_git(tmp_path: Path) -> None:
    """Verify ignore checks raise instead of guessing outside a Git checkout."""

    gate = load_quality_gate_module()
    candidate = tmp_path / "file.txt"
    candidate.write_text("x\n", encoding="utf-8")

    with pytest.raises(RuntimeError):
        gate.git_ignored_paths(tmp_path, [candidate])


def test_ignore_check_fails_closed_when_git_rejects_batch(
    monkeypatch, superproject: Path
) -> None:
    """Verify a failed `git check-ignore` batch raises instead of passing."""

    gate = load_quality_gate_module()
    real_run = subprocess.run

    def failing_check_ignore(command, *args, **kwargs):
        """Fail `git check-ignore` and pass every other command through."""
        if command[:2] == ["git", "check-ignore"]:
            return subprocess.CompletedProcess(command, 128, b"", b"fatal: boom")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(gate.subprocess, "run", failing_check_ignore)

    with pytest.raises(RuntimeError, match="check-ignore failed"):
        gate.git_ignored_paths(superproject, [superproject / "app.py"])


def test_fingerprint_is_keyed_by_platform_and_python_version(
    monkeypatch, tmp_path: Path
) -> None:
    """Verify a pass cached on another platform or Python version never matches."""

    gate = load_quality_gate_module()
    source = tmp_path / "app.py"
    source.write_text("print('app')\n", encoding="utf-8")
    major, minor = sys.version_info[:2]

    assert gate.execution_context() == (
        f"platform:{sys.platform}\npython:{major}.{minor}\n"
    )
    baseline = gate.fingerprint_files(tmp_path, [source], "pytest")
    assert gate.fingerprint_files(tmp_path, [source], "pytest") == baseline

    for platform, version in (
        ("other-platform", sys.version_info),
        (sys.platform, (major, minor + 1, 0)),
    ):
        monkeypatch.setattr(
            gate, "sys", SimpleNamespace(platform=platform, version_info=version)
        )
        assert gate.fingerprint_files(tmp_path, [source], "pytest") != baseline

    # The patch level does not change which tests run, so it shares a key.
    monkeypatch.setattr(
        gate,
        "sys",
        SimpleNamespace(platform=sys.platform, version_info=(major, minor, 99)),
    )
    assert gate.fingerprint_files(tmp_path, [source], "pytest") == baseline
