"""The disposable demo disk: download once, verify the pinned hash, attach
read-only.

The image is not shipped in the wheel. A hash mismatch deletes the file and
refuses; there is no mirror fallback and no unverified attach.
"""

from __future__ import annotations

import hashlib
import os
import re
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ntfsmnt.cli import real_run

DEMO_URL = "https://github.com/AkshayHarti/ntfs-mounter/releases/download/demo-v1/demo.img"
# The pinned hash of the `demo.img` asset on the `demo-v1` release. An empty
# hash refuses rather than attaching something unverified.
DEMO_SHA256 = "8713b7aceac000c8eaf6dd35aca63373b605bb6dd7eccf2890c24a5925d9aba4"

DEVICE = re.compile(r"/dev/disk\d+")


class DemoError(Exception):
    pass


def cache_path() -> Path:
    return Path.home() / "Library" / "Caches" / "ntfsmnt" / "demo.img"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    part = destination.with_suffix(destination.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, \
                part.open("wb") as handle:
            for block in iter(lambda: response.read(1 << 20), b""):
                handle.write(block)
        os.replace(part, destination)
    finally:
        part.unlink(missing_ok=True)


def ensure_image(*, fetch: Callable[[str], None] | None = None,
                 path: Path | None = None, url: str = DEMO_URL,
                 sha256: str = DEMO_SHA256) -> Path:
    if not sha256:
        raise DemoError(
            "the demo image is not published yet; no unverified image is attached")
    destination = path or cache_path()
    if destination.exists():
        if _sha256(destination) == sha256:
            return destination
        destination.unlink()

    destination.parent.mkdir(parents=True, exist_ok=True)
    if fetch is not None:
        fetch(destination)
    else:
        _download(url, destination)

    if _sha256(destination) != sha256:
        destination.unlink(missing_ok=True)
        raise DemoError("the demo image failed its hash check; deleted it")
    return destination


def attach_image(path: Path, *, run: Callable = real_run) -> str:
    code, output = run(["hdiutil", "attach", str(path), "-readonly", "-nomount"])
    if code != 0:
        raise DemoError(f"hdiutil could not attach the demo image: {output.strip()}")
    match = DEVICE.search(output)
    if match is None:
        raise DemoError("hdiutil did not report a device for the demo image")
    return match.group(0)
