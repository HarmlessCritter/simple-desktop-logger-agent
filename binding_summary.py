from __future__ import annotations

from copy import deepcopy
from typing import Any

from browser_tracking import (
    browser_detail_key,
    favicon_url,
    is_supported_browser,
    site_display_name,
)
from focus_watcher import FocusInfo


GROUP_KEY_PREFIX = "group:"
BROWSER_KEY_PREFIX = "browser:"
OTHER_BROWSER_SOURCE_KEY = "browser:other"


def group_activity_key(group_id: str) -> str:
    return f"{GROUP_KEY_PREFIX}{group_id}"


def binding_target_for_source(
    source_key: str,
    groups: list[dict[str, Any]],
    bindings: dict[str, str],
) -> dict[str, Any] | None:
    """Return the display group for one persisted source, if it is bound."""
    group_id = bindings.get(source_key)
    if not group_id:
        return None

    group = next((item for item in groups if str(item["groupId"]) == group_id), None)
    if group is None:
        return None

    return {
        "activityKey": group_activity_key(group_id),
        "displayName": str(group["displayName"]),
        "kind": "user_group",
        "groupId": group_id,
        "iconId": str(group.get("iconId") or "folder"),
    }


def apply_bindings_to_totals(
    raw_totals: dict[str, dict[str, Any]],
    groups: list[dict[str, Any]],
    bindings: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Build display totals without changing the persisted source totals."""
    if not groups:
        return raw_totals

    totals = deepcopy(raw_totals)
    groups_by_id = {str(group["groupId"]): group for group in groups}
    group_totals = {
        group_activity_key(group_id): {
            "activityKey": group_activity_key(group_id),
            "displayName": str(group["displayName"]),
            "iconId": str(group.get("iconId") or "folder"),
            "kind": "user_group",
            "groupId": group_id,
            "totalMs": 0,
            "groupItems": [],
        }
        for group_id, group in groups_by_id.items()
    }

    _move_bound_activities(totals, group_totals, bindings)
    _move_bound_browser_details(totals, group_totals, bindings)

    for group_key, group_total in group_totals.items():
        group_total["groupItems"].sort(key=lambda item: int(item.get("totalMs") or 0), reverse=True)
        totals[group_key] = group_total

    return totals


def current_summary_target(
    current: FocusInfo | None,
    current_browser_detail: dict[str, Any] | None,
    started_at: int | None,
    current_activity_key: str,
    groups: list[dict[str, Any]],
    bindings: dict[str, str],
) -> dict[str, Any] | None:
    if not current or started_at is None:
        return None

    source_key = _current_source_key(current, current_browser_detail, current_activity_key)
    if not source_key:
        return None

    target = binding_target_for_source(source_key, groups, bindings)
    if target is None:
        return None

    return {
        "activityKey": target["activityKey"],
        "displayName": target["displayName"],
        "startedAt": started_at,
        "sourceKey": source_key,
    }


def ensure_current_group_item(
    totals: dict[str, dict[str, Any]],
    target: dict[str, Any] | None,
    current: FocusInfo | None,
    current_browser_detail: dict[str, Any] | None,
) -> None:
    if not target:
        return

    group = totals.get(str(target["activityKey"]))
    if not group:
        return

    source_key = str(target["sourceKey"])
    items = group.setdefault("groupItems", [])
    if any(str(item.get("sourceKey") or "") == source_key for item in items):
        return

    item = _current_group_item(source_key, current, current_browser_detail)
    if item is not None:
        items.append(item)


def _move_bound_activities(
    totals: dict[str, dict[str, Any]],
    group_totals: dict[str, dict[str, Any]],
    bindings: dict[str, str],
) -> None:
    for total_key, item in list(totals.items()):
        activity_key = str(item.get("activityKey") or "")
        group_id = bindings.get(activity_key)
        if not group_id:
            continue

        group = group_totals.get(group_activity_key(group_id))
        if group is None:
            continue

        group_item = {
            "sourceKey": activity_key,
            "sourceType": "activity",
            "label": str(item.get("displayName") or item.get("focus", {}).get("display_name") or activity_key),
            "totalMs": int(item.get("totalMs") or 0),
            "focus": item.get("focus"),
        }
        _append_or_merge_group_item(group, group_item)
        group["totalMs"] += group_item["totalMs"]
        totals.pop(total_key, None)


def _move_bound_browser_details(
    totals: dict[str, dict[str, Any]],
    group_totals: dict[str, dict[str, Any]],
    bindings: dict[str, str],
) -> None:
    for total_key, item in list(totals.items()):
        details = item.get("browserDetails")
        if not isinstance(details, list):
            continue

        remaining_details: list[dict[str, Any]] = []
        moved_total_ms = 0
        for detail in details:
            source_key = str(detail.get("key") or "")
            group_id = bindings.get(source_key)
            group = group_totals.get(group_activity_key(group_id)) if group_id else None
            if group is None:
                remaining_details.append(detail)
                continue

            total_ms = int(detail.get("totalMs") or 0)
            group_item = {
                "sourceKey": source_key,
                "sourceType": "browser",
                "label": str(detail.get("label") or source_key),
                "host": str(detail.get("host") or ""),
                "faviconUrl": str(detail.get("faviconUrl") or ""),
                "processName": str(detail.get("processName") or item.get("focus", {}).get("process_name") or ""),
                "windowTitle": str(detail.get("windowTitle") or ""),
                "totalMs": total_ms,
            }
            _append_or_merge_group_item(group, group_item)
            group["totalMs"] += total_ms
            moved_total_ms += total_ms

        if moved_total_ms:
            item["totalMs"] = max(0, int(item.get("totalMs") or 0) - moved_total_ms)
        item["browserDetails"] = remaining_details
        if not remaining_details and int(item.get("totalMs") or 0) == 0:
            totals.pop(total_key, None)


def _append_or_merge_group_item(group: dict[str, Any], candidate: dict[str, Any]) -> None:
    items = group.setdefault("groupItems", [])
    source_key = str(candidate["sourceKey"])
    existing = next((item for item in items if str(item.get("sourceKey") or "") == source_key), None)
    if existing is None:
        items.append(candidate)
        return

    existing["totalMs"] = int(existing.get("totalMs") or 0) + int(candidate.get("totalMs") or 0)


def _current_source_key(
    current: FocusInfo,
    current_browser_detail: dict[str, Any] | None,
    current_activity_key: str,
) -> str:
    if is_supported_browser(current.process_name):
        source_key = browser_detail_key(current_browser_detail)
        return "" if source_key == OTHER_BROWSER_SOURCE_KEY else source_key

    return current_activity_key


def _current_group_item(
    source_key: str,
    current: FocusInfo | None,
    current_browser_detail: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if source_key.startswith(BROWSER_KEY_PREFIX):
        if not current_browser_detail:
            return None
        host = str(current_browser_detail.get("host") or "")
        status = str(current_browser_detail.get("tracking_status") or "other")
        return {
            "sourceKey": source_key,
            "sourceType": "browser",
            "label": site_display_name(host, status),
            "host": host,
            "faviconUrl": favicon_url(host, status),
            "processName": current.process_name if current else "",
            "windowTitle": str(current_browser_detail.get("title") or "") if status == "tracked" else "",
            "totalMs": 0,
        }

    if current is None:
        return None
    return {
        "sourceKey": source_key,
        "sourceType": "activity",
        "label": current.display_name,
        "totalMs": 0,
        "focus": {
            "hwnd": current.hwnd,
            "pid": current.pid,
            "process_name": current.process_name,
            "process_path": current.process_path,
            "window_class": current.window_class,
            "window_title": current.window_title,
            "display_name": current.display_name,
        },
    }
