"""Semantic Versioning 2.0.0 parsing and precedence."""

import functools
import re

_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def is_valid(version: str) -> bool:
    return isinstance(version, str) and _PATTERN.match(version) is not None


def _key(version: str):
    m = _PATTERN.match(version)
    if m is None:
        raise ValueError(f"not a semantic version: {version!r}")
    major, minor, patch, pre, _build = m.groups()
    return (int(major), int(minor), int(patch)), (pre.split(".") if pre else None)


def _compare_pre(a: list[str] | None, b: list[str] | None) -> int:
    # A version without a pre-release outranks one with it.
    if a is None and b is None:
        return 0
    if a is None:
        return 1
    if b is None:
        return -1
    for x, y in zip(a, b):
        if x == y:
            continue
        xn, yn = x.isdigit(), y.isdigit()
        if xn and yn:
            return -1 if int(x) < int(y) else 1
        if xn != yn:
            return -1 if xn else 1
        return -1 if x < y else 1
    return (len(a) > len(b)) - (len(a) < len(b))


def compare(a: str, b: str) -> int:
    """Returns -1, 0 or 1 as a is lower than, equal to or higher than b. Build metadata is ignored."""
    (core_a, pre_a), (core_b, pre_b) = _key(a), _key(b)
    if core_a != core_b:
        return -1 if core_a < core_b else 1
    return _compare_pre(pre_a, pre_b)


sort_key = functools.cmp_to_key(compare)
