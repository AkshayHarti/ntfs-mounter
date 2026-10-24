"""Tests for ``scripts/install-privileged.sh``.

Static checks against the frozen contract in
``docs/specs/2026-10-24-seamless-app-and-npm.md``, plus the read-only
``--check`` mode. Nothing here needs root, sudo, or the network, so the suite
runs on Linux CI too; nothing is installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install-privileged.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
RULE = "%admin ALL=(root) NOPASSWD: /usr/local/libexec/ntfsmnt/bin/ntfsmnt web *"


def _function(name: str) -> str:
    """One shell function's source, from its definition to its closing brace."""
    start = TEXT.index(f"{name}() {{")
    return TEXT[start : TEXT.index("\n}\n", start)]


def _code() -> str:
    """The script with comment lines removed, so order checks cannot be fooled
    by a comment that mentions the same words."""
    return "\n".join(
        line for line in TEXT.splitlines() if not line.lstrip().startswith("#")
    )


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_bash_syntax_ok() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


def test_strict_mode() -> None:
    assert "set -euo pipefail" in TEXT


def test_rule_line_is_exact_and_web_only() -> None:
    assert RULE.endswith("web *")
    assert "mount" not in RULE
    assert RULE.count("*") == 1
    grants = [line.strip() for line in TEXT.splitlines() if "NOPASSWD:" in line]
    assert len(grants) == 1, grants
    assert RULE in grants[0]


def test_rule_file_mode_and_owner() -> None:
    assert "0440" in TEXT
    assert "root" in TEXT
    assert "wheel" in TEXT
    install_line = next(line for line in TEXT.splitlines() if "install -m 0440" in line)
    assert "-o root" in install_line
    assert "-g wheel" in install_line
    assert "$SUDOERS_FILE" in install_line
    assert "/etc/sudoers.d/ntfsmnt" in TEXT


def test_visudo_validates_before_install() -> None:
    code = _code()
    assert code.index("visudo -cf") < code.index("install -m 0440")


def test_ancestors_must_be_root_owned_and_not_writable() -> None:
    # A NOPASSWD target under a user-writable prefix is passwordless root.
    body = _function("install_cli")
    assert body.index("require_trusted_ancestors") < body.index("find_uv")
    assert "untrusted_ancestors" in _code()
    assert "not root-owned or group/other-writable" in TEXT


def test_install_is_atomic() -> None:
    # Build beside the target and swap, so a failed install never leaves a
    # half-populated venv that the rule still points at.
    body = _function("install_cli")
    build = body.index("build_dir=$VENV_DIR.build.$$")
    swap = body.index('mv "$build_dir" "$VENV_DIR"')
    assert build < swap
    assert body.index('rm -rf "$VENV_DIR"') > build
    assert 'trap "rm -rf \'$build_dir\'; cleanup" EXIT' in body


def test_fix_shebangs_repoints_console_scripts(tmp_path) -> None:
    start = TEXT.index("fix_shebangs() {")
    func = TEXT[start : TEXT.index("\n}\n", start) + 3]
    old = tmp_path / "ntfsmnt.build.123"
    new = tmp_path / "ntfsmnt"
    for path in (old / "bin", new / "bin"):
        path.mkdir(parents=True)
    (new / "bin" / "python").symlink_to(sys.executable)
    script = new / "bin" / "ntfsmnt"
    script.write_text(f"#!{old}/bin/python\n")
    other = new / "bin" / "other"
    other.write_text("#!/bin/sh\n")

    subprocess.run(
        ["bash", "-c", func + '\nfix_shebangs "$OLD" "$NEW"'],
        env={**os.environ, "OLD": str(old), "NEW": str(new)},
        check=True,
    )

    assert script.read_text().splitlines()[0] == f"#!{new}/bin/python"
    assert other.read_text().splitlines()[0] == "#!/bin/sh"


def test_swap_rewrites_shebangs_and_smoke_tests_the_entry_point() -> None:
    body = _function("install_cli")
    assert body.index('mv "$build_dir" "$VENV_DIR"') < body.index("fix_shebangs")
    # A stale interpreter path must fail the install, not the app's alert.
    assert '"$CLI_BIN" --help' in body


def test_check_probes_the_full_start_argv() -> None:
    # The rule carries args (`web *`); a bare `web` probe never matches it.
    code = _code()
    assert 'sudo -n -l "$CLI_BIN" web --port 0 --owner' in code


def test_check_does_not_fail_on_an_unreadable_rule() -> None:
    # 0440 root:wheel is unreadable by the user running --check, which is the
    # normal case. The sudo -n -l probe is authoritative there.
    body = _function("run_check")
    assert "[[ ! -r $SUDOERS_FILE ]]" in body
    assert "is not readable as this user" in body
    assert "owned by root:wheel" in body


def test_visudo_is_required_before_touching_anything() -> None:
    body = _function("install_cli")
    assert body.index("have visudo") < body.index("rm -rf")
    assert body.index("have visudo") < body.index('chown -R root:wheel')


def test_uninstall_removes_rule_before_venv() -> None:
    body = _function("run_uninstall")
    rule = body.index('rm -f "$SUDOERS_FILE"')
    venv = body.index('rm -rf "$VENV_DIR"')
    assert rule < venv


def test_uninstall_is_idempotent() -> None:
    body = _function("run_uninstall")
    assert 'rm -f "$SUDOERS_FILE"' in body
    assert "was already absent" in body


def test_sdist_takes_precedence_over_version() -> None:
    body = _function("resolve_spec")
    assert body.index("$SDIST") < body.index("$VERSION")
    assert body.index("$VERSION") < body.index("$INIT_PY")


def test_version_falls_back_to_package_init() -> None:
    assert "ntfsmnt/__init__.py" in TEXT
    assert "ntfsmnt[web]==%s" in TEXT


def test_group_or_other_writable_check_uses_find() -> None:
    assert "-perm -0020 -o -perm -0002" in TEXT


def test_no_osascript() -> None:
    assert "osascript" not in TEXT


def test_no_kill_signal() -> None:
    assert "kill -9" not in TEXT
    assert "SIGKILL" not in TEXT


def test_no_repair_tools() -> None:
    for tool in ("ntfsfix", "ntfsresize", "mkntfs", "ntfswipe"):
        assert tool not in TEXT


def test_install_and_uninstall_require_root() -> None:
    assert "EUID -ne 0" in TEXT
    assert "must run as root" in TEXT
    assert "run: sudo " in TEXT
    # --check is dispatched before the guard, so it never needs root.
    code = _code()
    assert code.index("MODE == check") < code.index("\nrequire_root\n")


def test_not_root_refusal_names_the_sudo_command() -> None:
    if os.geteuid() == 0:
        pytest.skip("running as root, so the refusal cannot be observed")
    result = _run("--uninstall")
    assert result.returncode == 1
    assert "must run as root" in result.stderr
    assert "sudo ./scripts/install-privileged.sh --uninstall" in result.stderr


def test_help_exits_zero() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "--sdist" in result.stdout
    assert "--uninstall" in result.stdout


def test_unknown_option_exits_two() -> None:
    result = _run("--nonsense")
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_check_mode_exits_zero_unprivileged() -> None:
    if sys.platform != "darwin":
        pytest.skip("the installer is macOS-only; --check is exercised on macOS")
    result = _run("--check")
    assert result.returncode == 0, result.stderr
    assert "ok: macOS" in result.stdout
    assert "summary:" in result.stdout
    assert any(marker in result.stdout for marker in ("ok:", "FAIL:", "info:"))
    # install steps print "==>"; --check must not.
    assert "==>" not in result.stdout

def test_local_build_installs_the_checkout_not_a_same_numbered_release() -> None:
    body = _function("install_cli")
    assert "--local needs" in body or "needs uv" in body
    assert body.index("Build the local checkout") < body.index("resolve_spec")
    assert "uv\" build --out-dir" in body
    guard = _code()
    assert "--local and --version" in guard or "--local takes the version" in guard
    assert "--local            build the checkout" in TEXT  # documented in --help
