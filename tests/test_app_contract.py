"""The menu-bar app's launch contract, pinned against the other half of it.

The app, the sudoers installer, and the web server must agree on one command
line and one output line. These are static checks: they run on any OS and need
neither Swift nor root.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app" / "MenuBar.swift"
INSTALLER = ROOT / "scripts" / "install-privileged.sh"
BUILD = ROOT / "scripts" / "build-app.sh"
SERVER = ROOT / "ntfsmnt" / "web" / "app.py"

ROOT_CLI = "/usr/local/libexec/ntfsmnt/bin/ntfsmnt"
TEXT = APP.read_text(encoding="utf-8")


def test_root_cli_path_is_the_same_everywhere() -> None:
    assert ROOT_CLI in TEXT
    assert ROOT_CLI in INSTALLER.read_text(encoding="utf-8")


def test_start_argv_is_the_contract() -> None:
    assert 'child.arguments = ["-n", rootCLI] + webArguments' in TEXT
    assert '"web", "--port", "0", "--owner", "\\(getuid()):\\(getgid())"' in TEXT


def test_detection_probes_the_full_argv() -> None:
    # `web *` in sudoers needs matching arguments; a bare `web` never matches.
    assert 'probe.arguments = ["-n", "-l", rootCLI] + webArguments' in TEXT


def test_url_pattern_matches_the_server_output() -> None:
    server = SERVER.read_text(encoding="utf-8")
    assert 'print(f"ntfsmnt web: {url}", flush=True)' in server
    assert r"^ntfsmnt web: (http://\S+)$" in TEXT


def test_no_applescript_and_no_shell() -> None:
    assert "osascript" not in TEXT
    for shell in ('"/bin/sh"', '"/bin/zsh"', '"/bin/bash"', "shell: true"):
        assert shell not in TEXT


def test_stop_sends_sigterm_before_sigkill() -> None:
    start = TEXT.index("private func stopDashboard(")
    body = TEXT[start : TEXT.index("\n    private func waitUntilGone", start)]
    assert body.index("child.terminate()") < body.index("kill(descendant, SIGKILL)")
    # The forced signal is aimed at our own server tree, never a disk process.
    assert "children(of: pid)" in body


def test_bundle_ships_the_installer_and_pins_the_version() -> None:
    build = BUILD.read_text(encoding="utf-8")
    assert "install-privileged.sh" in build
    assert "install-privileged.command" in build
    assert '--version %s' in build
    assert "cmp -s" in build  # the bundled copy is byte-identical


def test_bundle_version_comes_from_the_package() -> None:
    build = BUILD.read_text(encoding="utf-8")
    assert "CFBundleShortVersionString" in build
    assert "PlistBuddy" in build
    assert "ntfsmnt/__init__.py" in build
