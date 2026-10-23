"""Packaging metadata regressions: single-source version, licence, entry point."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT = ROOT / "ntfsmnt" / "__init__.py"


def _project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _declared_version() -> str:
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("__version__ not found in ntfsmnt/__init__.py")


def test_name_and_entry_point() -> None:
    config = _project()
    assert config["project"]["name"] == "ntfsmnt"
    assert config["project"]["scripts"]["ntfsmnt"] == "ntfsmnt.cli:main"


def test_version_is_single_source() -> None:
    config = _project()
    assert "version" not in config["project"]
    assert config["project"]["dynamic"] == ["version"]
    assert config["tool"]["hatch"]["version"]["path"] == "ntfsmnt/__init__.py"
    assert INIT.is_file()
    version = _declared_version()
    assert isinstance(version, str)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version


def test_web_extra_unchanged() -> None:
    web = _project()["project"]["optional-dependencies"]["web"]
    for requirement in ("fastapi", "uvicorn", "jinja2"):
        assert any(requirement in spec for spec in web), requirement


def test_licence_declared() -> None:
    config = _project()
    assert config["project"]["license"] == "MIT"
    assert (ROOT / "LICENSE").is_file()
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


def test_runtime_is_stdlib_only() -> None:
    assert _project()["project"].get("dependencies", []) == []
