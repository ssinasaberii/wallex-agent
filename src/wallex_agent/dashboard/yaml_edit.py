"""Change scalar values in config/default.yaml in place, keeping every comment and the layout."""

from __future__ import annotations

import re
from typing import Any

_LINE = re.compile(r"^(?P<ind>\s*)(?P<key>[A-Za-z0-9_]+):(?P<sp>\s*)(?P<rest>.*)$")


def fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(round(v, 6))
    s = str(v)
    return s if re.fullmatch(r"[A-Za-z0-9_.\-]+", s) else '"' + s.replace('"', '\\"') + '"'


def _split_comment(rest: str) -> tuple[str, str]:
    q = None
    for i, ch in enumerate(rest):
        if ch in "\"'":
            q = None if q == ch else (ch if q is None else q)
        elif ch == "#" and q is None and (i == 0 or rest[i - 1] in " \t"):
            return rest[:i].rstrip(), rest[i:]
    return rest.rstrip(), ""


def set_values(text: str, values: dict[str, Any]) -> str:
    """values: dotted path -> new scalar. Raises KeyError if a path is missing or is not a scalar."""
    lines = text.splitlines(keepends=True)
    stack: list[tuple[int, str]] = []
    left = dict(values)
    for i, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        if not body.strip() or body.lstrip().startswith("#") or body.lstrip().startswith("-"):
            continue
        m = _LINE.match(body)
        if not m:
            continue
        ind = len(m["ind"])
        while stack and stack[-1][0] >= ind:
            stack.pop()
        path = ".".join([k for _, k in stack] + [m["key"]])
        val, comment = _split_comment(m["rest"])
        if path in left:
            if val == "" or val.startswith("{") or val.startswith("["):
                raise KeyError(f"{path} is not a scalar")
            new = fmt_value(left.pop(path))
            if comment:  # keep the comment in its column
                width = len(m["rest"]) - len(comment)
                new = new + " " * max(1, width - len(new))
            eol = raw[len(body):]
            lines[i] = f"{m['ind']}{m['key']}:{m['sp'] or ' '}{new}{comment}{eol}"
        if val == "":
            stack.append((ind, m["key"]))
    if left:
        raise KeyError(f"not found: {sorted(left)}")
    return "".join(lines)


def get_path(d: dict, path: str) -> Any:
    for k in path.split("."):
        d = d[k]
    return d
