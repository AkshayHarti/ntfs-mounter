"""The registry manifest has to describe the package that actually ships.

`server.json` is read by the MCP registry, not by ntfsmnt, so nothing at
runtime would notice if it drifted. These checks keep it honest.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ntfsmnt import __version__
from ntfsmnt.mcp import ACTION_TOOLS, SUPPORTED_TOOLS

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
TEXT = (ROOT / "server.json").read_text(encoding="utf-8")


def test_server_json_is_valid_and_version_matches() -> None:
    assert set(MANIFEST) >= {"name", "description", "version"}
    assert re.fullmatch(r"[a-zA-Z0-9.-]+/[a-zA-Z0-9._-]+", MANIFEST["name"])
    assert MANIFEST["version"] == __version__
    assert MANIFEST["description"].strip()


def test_package_is_pypi_stdio_running_mcp() -> None:
    (package,) = MANIFEST["packages"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == "ntfsmnt"
    assert package["version"] == __version__
    assert package["transport"] == {"type": "stdio"}
    assert package["runtimeHint"] == "uvx"
    values = [argument.get("value") for argument in package["packageArguments"]]
    assert values[-1] == "mcp"


def test_no_extra_or_fuse_t_bundling_claims() -> None:
    (package,) = MANIFEST["packages"]
    # The wheel is stdlib-only; the web UI is an extra and FUSE-T/ntfs-3g are
    # host prerequisites installed by scripts/setup.sh, never bundled.
    assert "environmentVariables" not in package
    assert "fileSha256" not in package
    assert "registryBaseUrl" not in package or package["registryBaseUrl"] == (
        "https://pypi.org")
    lowered = TEXT.lower()
    assert "ntfsmnt[web]" not in lowered
    assert "bundles" not in lowered
    assert "includes fuse-t" not in lowered
    assert "ships fuse-t" not in lowered


def test_tool_list_matches_supported_tools() -> None:
    publisher = MANIFEST["_meta"]["io.modelcontextprotocol.registry/publisher-provided"]
    listed = set(publisher["tools"])
    assert listed <= set(SUPPORTED_TOOLS)
    assert set(ACTION_TOOLS) <= listed
