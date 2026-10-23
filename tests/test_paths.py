import stat

from ntfsmnt import paths


def _executable(path):
    path.write_text("#!/bin/sh\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _dir(tmp_path, name):
    directory = tmp_path / name
    directory.mkdir()
    return str(directory)


def test_override_wins_over_path_and_candidates(tmp_path, monkeypatch):
    override = _executable(tmp_path / "custom-ntfs-3g")
    monkeypatch.setenv("NTFSMNT_NTFS3G", override)

    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    _executable(path_dir / "ntfs-3g")
    monkeypatch.setenv("PATH", str(path_dir))

    candidate = _executable(tmp_path / "candidate-ntfs-3g")
    monkeypatch.setattr(paths, "DEFAULT_CANDIDATES", (candidate,))

    assert paths.resolve_ntfs3g() == override


def test_candidates_win_before_path(tmp_path, monkeypatch):
    # The fixed candidates are first so a sudo-secure-path root process still
    # resolves the FUSE-T binary even when PATH lists a macFUSE-linked one.
    monkeypatch.delenv("NTFSMNT_NTFS3G", raising=False)

    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    _executable(path_dir / "ntfs-3g")
    monkeypatch.setenv("PATH", str(path_dir))

    candidate = _executable(tmp_path / "candidate-ntfs-3g")
    monkeypatch.setattr(paths, "DEFAULT_CANDIDATES", (candidate,))

    assert paths.resolve_ntfs3g() == candidate


def test_path_binary_wins_when_no_candidate_matches(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFSMNT_NTFS3G", raising=False)
    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    on_path = _executable(path_dir / "ntfs-3g")
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(
        paths,
        "DEFAULT_CANDIDATES",
        (str(tmp_path / "missing-a"), str(tmp_path / "missing-b")),
    )

    assert paths.resolve_ntfs3g() == on_path

def test_first_candidate_used_when_path_misses(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFSMNT_NTFS3G", raising=False)
    monkeypatch.setenv("PATH", _dir(tmp_path, "empty"))

    first = _executable(tmp_path / "homebrew-ntfs-3g")
    second = _executable(tmp_path / "local-ntfs-3g")
    monkeypatch.setattr(paths, "DEFAULT_CANDIDATES", (first, second))

    assert paths.resolve_ntfs3g() == first


def test_non_executable_candidate_is_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFSMNT_NTFS3G", raising=False)
    monkeypatch.setenv("PATH", _dir(tmp_path, "empty"))

    plain = tmp_path / "plain-ntfs-3g"
    plain.write_text("not a program\n")
    executable = _executable(tmp_path / "executable-ntfs-3g")
    monkeypatch.setattr(paths, "DEFAULT_CANDIDATES", (str(plain), executable))

    assert paths.resolve_ntfs3g() == executable


def test_final_fallback_keeps_the_previous_default(tmp_path, monkeypatch):
    monkeypatch.delenv("NTFSMNT_NTFS3G", raising=False)
    monkeypatch.setenv("PATH", _dir(tmp_path, "empty"))
    monkeypatch.setattr(
        paths,
        "DEFAULT_CANDIDATES",
        (str(tmp_path / "missing-a"), str(tmp_path / "missing-b")),
    )

    assert paths.FALLBACK == "/usr/local/bin/ntfs-3g"
    assert paths.resolve_ntfs3g() == "/usr/local/bin/ntfs-3g"


def test_probe_sits_beside_an_absolute_binary():
    assert paths.resolve_probe("/usr/local/bin/ntfs-3g") == (
        "/usr/local/bin/ntfs-3g.probe"
    )
    assert paths.resolve_probe("/opt/homebrew/bin/ntfs-3g") == (
        "/opt/homebrew/bin/ntfs-3g.probe"
    )


def test_probe_for_a_bare_name_stays_bare():
    assert paths.resolve_probe("ntfs-3g") == "ntfs-3g.probe"


def test_module_constants_are_consistent():
    assert paths.NTFS3G_PROBE == paths.resolve_probe(paths.NTFS3G)


def test_empty_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("NTFSMNT_NTFS3G", "")

    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    on_path = _executable(path_dir / "ntfs-3g")
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(paths, "DEFAULT_CANDIDATES", ())

    assert paths.resolve_ntfs3g() == on_path


def test_override_that_is_not_a_file_is_ignored(tmp_path, monkeypatch):
    # A directory is not a binary: fall through to PATH rather than exec it.
    monkeypatch.setenv("NTFSMNT_NTFS3G", _dir(tmp_path, "not-a-binary"))

    path_dir = tmp_path / "bin"
    path_dir.mkdir()
    on_path = _executable(path_dir / "ntfs-3g")
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(paths, "DEFAULT_CANDIDATES", ())

    assert paths.resolve_ntfs3g() == on_path
