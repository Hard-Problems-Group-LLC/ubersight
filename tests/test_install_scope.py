from ubersight.install_scope import (
    CODEX_HOME_DIRNAME,
    default_pyenv_root,
    default_tool_venv,
    default_user_bin_dir,
    describe_user_home_source,
    resolve_user_home,
)


def test_default_user_paths_follow_selected_home(tmp_path):
    """Verify default user paths follow selected home."""
    user_home = tmp_path / "operator"

    assert default_user_bin_dir(user_home) == user_home / ".local" / "bin"
    assert default_tool_venv(user_home, "ubersight") == (
        user_home / ".local" / "share" / "ubersight" / "venv"
    )
    assert default_pyenv_root(user_home) == user_home / ".pyenv"


def test_resolve_user_home_rejects_repo_local_codex_home_by_default(
    tmp_path,
    monkeypatch,
):
    """Verify resolve user home rejects repo local codex home by default."""
    repo_root = tmp_path / "repo"
    isolated_home = repo_root / CODEX_HOME_DIRNAME
    repo_root.mkdir()
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))

    try:
        resolve_user_home(repo_root)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected isolated-home resolution to fail.")

    assert "--user-home" in message
    assert "--allow-isolated-home" in message


def test_resolve_user_home_accepts_explicit_override_from_isolated_home(
    tmp_path,
    monkeypatch,
):
    """Verify resolve user home accepts explicit override from isolated home."""
    repo_root = tmp_path / "repo"
    isolated_home = repo_root / CODEX_HOME_DIRNAME
    operator_home = tmp_path / "operator"
    repo_root.mkdir()
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))

    resolved = resolve_user_home(repo_root, operator_home)

    assert resolved == operator_home.resolve()
    assert (
        describe_user_home_source(repo_root, resolved, operator_home)
        == "explicit --user-home target"
    )


def test_resolve_user_home_allows_explicit_isolated_home_opt_in(
    tmp_path,
    monkeypatch,
):
    """Verify resolve user home allows explicit isolated home opt in."""
    repo_root = tmp_path / "repo"
    isolated_home = repo_root / CODEX_HOME_DIRNAME
    repo_root.mkdir()
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))

    resolved = resolve_user_home(repo_root, allow_isolated_home=True)

    assert resolved == isolated_home.resolve()
    assert (
        describe_user_home_source(repo_root, resolved, None)
        == "current repo-local Codex home"
    )
