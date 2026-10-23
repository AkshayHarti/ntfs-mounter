"""npm channel regressions: version sync, wrapper shape, tokenless workflow."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NPM = ROOT / "npm"
PACKAGE = NPM / "package.json"
WRAPPER = NPM / "bin" / "ntfsmnt.js"
WORKFLOW = ROOT / ".github" / "workflows" / "npm.yml"
INIT = ROOT / "ntfsmnt" / "__init__.py"


def _declared_version() -> str:
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("__version__ not found in ntfsmnt/__init__.py")


def _package() -> dict:
    return json.loads(PACKAGE.read_text(encoding="utf-8"))


def test_version_matches_python() -> None:
    assert _package()["version"] == _declared_version()


def test_name_and_bin() -> None:
    package = _package()
    assert package["name"] == "ntfsmnt"
    assert package["bin"] == {"ntfsmnt": "bin/ntfsmnt.js"}


def test_no_dependencies() -> None:
    package = _package()
    assert "dependencies" not in package
    assert "devDependencies" not in package


def test_bin_points_at_a_node_script() -> None:
    assert WRAPPER.is_file()
    first_line = WRAPPER.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env node"


def test_files_covers_every_bin_target() -> None:
    package = _package()
    files = package["files"]
    assert "bin" in files
    for target in package["bin"].values():
        assert any(target == entry or target.startswith(entry + "/") for entry in files), target


def test_wrapper_never_uses_osascript() -> None:
    assert "osascript" not in WRAPPER.read_text(encoding="utf-8").lower()


def test_workflow_is_tokenless_oidc() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "id-token: write" in workflow
    assert "NPM_TOKEN" not in workflow
    assert "secrets." not in workflow
    assert "--provenance" in workflow
    assert "ubuntu-24.04" in workflow


def test_node_test_suite_present() -> None:
    assert (NPM / "test" / "proxy.test.js").is_file()


def test_tests_are_hermetic_and_skip_nothing() -> None:
    # Fixed absolute candidates would otherwise answer on a host that has a
    # real CLI, which is why the suite sets NTFSMNT_CANDIDATES.
    tests = (NPM / "test" / "proxy.test.js").read_text(encoding="utf-8")
    assert "NTFSMNT_CANDIDATES" in tests
    assert "skip:" not in tests
    assert "SYSTEM_CANDIDATES" not in tests


def test_wrapper_guards_recursion_and_custom_prefixes() -> None:
    wrapper = WRAPPER.read_text(encoding="utf-8")
    assert "NTFSMNT_CANDIDATES" in wrapper
    assert "NTFSMNT_PROXY_ACTIVE" in wrapper
    assert "process.kill(process.pid, signal)" in wrapper


def test_npm_docs_match_the_wrapper() -> None:
    docs = (ROOT / "docs" / "NPM.md").read_text(encoding="utf-8")
    assert "NTFSMNT_CANDIDATES" in docs
    assert "NTFSMNT_PROXY_ACTIVE" in docs

