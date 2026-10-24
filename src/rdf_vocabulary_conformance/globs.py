"""Path patterns over forward-slash repository paths.

`*` and `?` stay inside one path segment; `**` crosses segments (`a/**/b`
also matches `a/b`). Nothing here touches a filesystem.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**", i):
                i += 2
                if pattern.startswith("/", i):
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z", re.DOTALL)


def has_wildcard(pattern: str) -> bool:
    return "*" in pattern or "?" in pattern


def matches(pattern: str, path: str) -> bool:
    return _compile(pattern).match(path) is not None


def static_directory(pattern: str) -> str:
    """The directory prefix before the first segment containing a wildcard ('' = root)."""
    parts = pattern.split("/")
    prefix: list[str] = []
    for part in parts[:-1]:
        if has_wildcard(part):
            break
        prefix.append(part)
    return "/".join(prefix)
