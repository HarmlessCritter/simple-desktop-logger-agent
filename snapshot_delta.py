from __future__ import annotations

from copy import deepcopy
from typing import Any


def build_snapshot_delta(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any]:
    sets: list[dict[str, Any]] = []
    removes: list[list[str]] = []

    def walk(previous: Any, current: Any, path: list[str]) -> None:
        if isinstance(previous, dict) and isinstance(current, dict):
            for key in previous.keys() - current.keys():
                removes.append([*path, key])
            for key, value in current.items():
                if key not in previous:
                    sets.append({"path": [*path, key], "value": deepcopy(value)})
                else:
                    walk(previous[key], value, [*path, key])
            return

        if previous != current:
            sets.append({"path": path, "value": deepcopy(current)})

    if before is None:
        sets.append({"path": [], "value": deepcopy(after)})
    else:
        walk(before, after, [])
    return {"set": sets, "remove": removes}


def apply_snapshot_delta(snapshot: dict[str, Any] | None, delta: dict[str, Any]) -> dict[str, Any]:
    result: Any = deepcopy(snapshot) if snapshot is not None else {}
    for operation in delta.get("set", []):
        path = operation["path"]
        value = deepcopy(operation["value"])
        if not path:
            result = value
            continue
        target = result
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = value
    for path in delta.get("remove", []):
        if not path:
            result = {}
            continue
        target = result
        for key in path[:-1]:
            target = target.get(key, {})
        target.pop(path[-1], None)
    return result
