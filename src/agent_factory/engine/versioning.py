"""Dependency-free engine range checks for repository PEP 440 specifiers."""

import json
import re
from pathlib import Path

from agent_factory import __version__


class VersionMismatch(ValueError):
    exit_code = 4


def _version(value: str) -> tuple:
    match = re.fullmatch(
        r"v?(?:(\d+)!)?(\d+(?:\.\d+)*)(?:[-_.]?(a|b|rc|alpha|beta|c|pre|preview)[-_.]?(\d*))?"
        r"(?:[-_.]?(post|rev|r)[-_.]?(\d*)|-(\d+))?(?:[-_.]?dev[-_.]?(\d*))?(?:\+([a-z0-9._-]+))?",
        value.strip().lower())
    if not match:
        raise ValueError(f"Invalid engine version: {value}")
    epoch, release, pre, number, post, post_n, dash_post, dev, local = match.groups()
    parts = tuple(map(int, release.split(".")))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    pre_rank = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "rc": 2, "c": 2, "pre": 2, "preview": 2}
    prerelease = (pre_rank[pre], int(
        number or 0)) if pre else ((-1, 0) if dev is not None and not (post or dash_post) else
                                   (3, 0))
    postrelease = int(post_n or dash_post or 0) if post or dash_post else -1
    development = int(dev or 0) if dev is not None else float("inf")
    local_key = tuple((1, int(p)) if p.isdecimal() else (0, p)
                      for p in re.split(r"[-_.]", local)) if local else ()
    return int(epoch or 0), parts, prerelease, postrelease, development, local_key


def _prefix(parts: tuple[int, ...], count: int) -> tuple[int, ...]:
    return (parts + (0, ) * count)[:count]


def matches(version: str, specifier: str) -> bool:
    actual = _version(version)
    for clause in specifier.split(","):
        match = re.fullmatch(r"\s*(===|~=|==|!=|<=|>=|<|>)\s*(\S+)\s*", clause)
        if not match:
            raise ValueError(f"Invalid engine specifier: {specifier}")
        op, wanted = match.groups()
        if op == "===":
            passed = version.lower() == wanted.lower()
        elif wanted.endswith(".*") and op in {"==", "!="}:
            prefix = wanted[:-2]
            if not re.fullmatch(r"(?:\d+!)?\d+(?:\.\d+)*", prefix):
                raise ValueError(f"Invalid wildcard engine specifier: {wanted}")
            target = _version(prefix)
            count = len(prefix.split("!")[-1].split("."))
            equal = actual[0] == target[0] and _prefix(actual[1], count) == _prefix(
                target[1], count)
            passed = equal if op == "==" else not equal
        else:
            if "+" in wanted and op not in {"==", "!="}:
                raise ValueError(f"Local versions are not allowed with {op}")
            target = _version(wanted)
            left = actual if "+" in wanted else actual[:-1]
            right = target if "+" in wanted else target[:-1]
            if op == "~=":
                release = wanted.split("!")[-1]
                numbers = re.match(r"\d+(?:\.\d+)+", release)
                if numbers is None:
                    raise ValueError("~= requires at least two release components")
                count = len(numbers[0].split(".")) - 1
                passed = left >= right and actual[0] == target[0] and _prefix(
                    actual[1], count) == _prefix(target[1], count)
            else:
                passed = {
                    "==": left == right,
                    "!=": left != right,
                    ">=": left >= right,
                    "<=": left <= right,
                    ">": left > right,
                    "<": left < right
                }[op]
                # Exclusive bounds exclude pre/post releases of the same release.
                if actual[:2] == target[:2]:
                    if op == "<" and target[2] == (3, 0) and actual[2] < (3, 0):
                        passed = False
                    if op == ">" and actual[3] > target[3] and target[3] < 0:
                        passed = False
        if not passed:
            return False
    return True


def check(root: Path, *, version: str = __version__, new_jobs: bool = False) -> None:
    raw = json.loads((root / "factory/config.json").read_text(encoding="utf-8-sig"))
    if new_jobs and raw.get("version", 1) != 2:
        raise VersionMismatch(f"{root.name} needs `factory upgrade` before starting new jobs")
    required = raw.get("engine")
    if required is None:
        if new_jobs:
            raise VersionMismatch(f"{root.name} needs `factory upgrade` (missing engine specifier)")
        return
    if not matches(version, required):
        minimum = re.search(r"(?:>=|~=|==)\s*(\d+)", required)
        major = minimum[1] if minimum else "1"
        raise VersionMismatch(
            f"{root.name} needs agent-factory {required}; installed {version}. "
            f"Install a matching version: `uv tool install agent-factory-engine=={major}.*`")
