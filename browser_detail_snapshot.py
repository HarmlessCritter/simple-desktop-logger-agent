from __future__ import annotations

from typing import Any

from activity_store import serialize_focus
from browser_tracking import (
    browser_detail_key,
    favicon_url,
    normalize_browser_host,
    site_display_name,
)
from focus_watcher import FocusInfo


def current_browser_detail_payload(
    focus: FocusInfo | None,
    browser_detail: dict[str, Any] | None,
    started_at: int | None,
    parent_activity_key: str,
) -> dict[str, Any] | None:
    if not focus or not browser_detail or started_at is None:
        return None

    host = str(browser_detail.get("host") or "")
    status = str(browser_detail.get("tracking_status") or "other")
    normalized_host = normalize_browser_host(host)
    return {
        "parentActivityKey": parent_activity_key,
        "parentDisplayName": focus.display_name,
        "key": browser_detail_key(browser_detail),
        "label": site_display_name(host, status),
        "host": normalized_host,
        "faviconUrl": favicon_url(host, status),
        "trackingStatus": status if normalized_host else "other",
        "startedAt": started_at,
    }


def inject_current_browser_detail(
    totals: dict[str, dict[str, Any]],
    detail: dict[str, Any] | None,
    focus: FocusInfo | None,
) -> None:
    """Add an in-progress browser site to an open-ended snapshot only."""
    if detail is None:
        return

    activity_key = str(detail["parentActivityKey"])
    parent = next(
        (
            item
            for item in totals.values()
            if str(item.get("activityKey") or "") == activity_key
        ),
        None,
    )
    if parent is None:
        serialized_focus = serialize_focus(focus)
        if serialized_focus is None:
            return
        parent = {
            "activityKey": activity_key,
            "totalMs": 0,
            "focus": serialized_focus,
            "browserDetails": [],
        }
        totals[str(detail["parentDisplayName"])] = parent

    browser_details = parent.setdefault("browserDetails", [])
    detail_key = str(detail["key"])
    if any(str(item.get("key") or "") == detail_key for item in browser_details):
        return

    browser_details.append(
        {
            "key": detail_key,
            "label": detail["label"],
            "host": detail["host"],
            "faviconUrl": detail["faviconUrl"],
            "trackingStatus": detail["trackingStatus"],
            "totalMs": 0,
        }
    )
