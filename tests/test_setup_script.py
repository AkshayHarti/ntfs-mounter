"""Tests for ``scripts/setup.sh``.

``--check`` is read-only and exits 0 on every platform, so these run on Linux
CI too. Nothing here installs anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "setup.sh"


def _check() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_check_mode_exits_zero() -> None:
    result = _check()
    assert result.returncode == 0, result.stderr
    assert "FUSE-T" in result.stdout
    assert "ntfs-3g" in result.stdout
    assert any(marker in result.stdout for marker in ("ok:", "MISSING:", "WARN:"))


def test_check_mode_reports_platform_appropriate_state() -> None:
    result = _check()
    if sys.platform == "darwin":
        assert "ok: macOS" in result.stdout
        assert "ok: FUSE-T" in result.stdout
        assert "ok: ntfs-3g" in result.stdout
        assert "links libfuse-t" in result.stdout
    else:
        assert "WARN:" in result.stdout
        assert "macOS is required" in result.stdout


def test_unknown_option_exits_2() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--nonsense"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


def test_no_osascript() -> None:
    assert "osascript" not in SCRIPT.read_text(encoding="utf-8")
