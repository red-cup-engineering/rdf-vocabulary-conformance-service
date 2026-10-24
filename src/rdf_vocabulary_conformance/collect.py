"""The filesystem edge: read exactly the files a configuration names into a path-to-bytes map.

Only the CLI uses this. Walks are bounded to the static directory of each
pattern (never the whole repository unless a pattern starts with a wildcard),
never follow symlinks out of the root, and skip `.git`.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import globs

MAX_FILE_BYTES = 32 * 1024 * 1024
SKIPPED_DIRECTORIES = {".git"}


def _inside(root_real: str, path: str) -> bool:
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real + os.sep)


def collect_files(root: Path, patterns: list[str]) -> dict[str, bytes]:
    root_real = os.path.realpath(root)
    found: dict[str, bytes] = {}

    def take(relative: str) -> None:
        full = os.path.join(root_real, *relative.split("/"))
        if relative in found or not os.path.isfile(full) or not _inside(root_real, full):
            return
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return
        with open(full, "rb") as handle:
            found[relative] = handle.read()

    for pattern in patterns:
        if not globs.has_wildcard(pattern):
            take(pattern)
            continue
        base = globs.static_directory(pattern)
        start = os.path.join(root_real, *base.split("/")) if base else root_real
        if not os.path.isdir(start) or not _inside(root_real, start):
            continue
        for directory, subdirectories, names in os.walk(start, followlinks=False):
            subdirectories[:] = sorted(d for d in subdirectories if d not in SKIPPED_DIRECTORIES)
            for name in sorted(names):
                relative = os.path.relpath(os.path.join(directory, name), root_real).replace(os.sep, "/")
                if globs.matches(pattern, relative):
                    take(relative)
    return dict(sorted(found.items()))
