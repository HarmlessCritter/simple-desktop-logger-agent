from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from browser_tracking import (
    OTHER_SITE_LABEL,
    browser_detail_key,
    favicon_url,
    is_supported_browser,
    normalize_browser_host,
    raw_browser_host,
    site_display_name,
)
from focus_watcher import FocusInfo


APP_DATA_DIR = Path.home() / "AppData" / "Local" / "SimpleDesktopLogger"
DB_PATH = APP_DATA_DIR / "activity.db"
KST = timezone(timedelta(hours=9))
WINDOWS_OPERATION = "\uc708\ub3c4\uc6b0 \uc870\uc791"
FILE_EXPLORER = "\ud30c\uc77c \ud0d0\uc0c9"
DEFAULT_GROUP_ICON_ID = "folder"
GROUP_ICON_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def now_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def start_of_local_day_ms() -> int:
    local_now = datetime.now(tz=KST)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.astimezone(timezone.utc).timestamp() * 1000)


def normalize_activity_display_name(display_name: str) -> str:
    if display_name == FILE_EXPLORER:
        return WINDOWS_OPERATION
    return display_name


def normalize_activity_key(activity_key: str) -> str:
    if activity_key == FILE_EXPLORER or activity_key == FILE_EXPLORER.lower():
        return WINDOWS_OPERATION
    return activity_key


class ActivityStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._ignored_browser_source_keys: set[str] = set()
        self._initialize()
        self._refresh_ignored_browser_source_keys()

    def _initialize(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at INTEGER NOT NULL,
                ended_at INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                activity_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                process_name TEXT NOT NULL,
                process_path TEXT NOT NULL DEFAULT '',
                window_class TEXT NOT NULL,
                window_title TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ignored_activity_rules (
                activity_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS browser_session_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_session_id INTEGER NOT NULL,
                browser_name TEXT NOT NULL,
                url TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                tracking_status TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS binding_groups (
                group_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                icon_id TEXT NOT NULL DEFAULT 'folder',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_bindings (
                source_key TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(group_id) REFERENCES binding_groups(group_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ignored_browser_detail_rules (
                source_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(activity_sessions)").fetchall()
        }
        if "process_path" not in columns:
            self.connection.execute("ALTER TABLE activity_sessions ADD COLUMN process_path TEXT NOT NULL DEFAULT ''")

        binding_group_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(binding_groups)").fetchall()
        }
        if "icon_id" not in binding_group_columns:
            self.connection.execute(
                "ALTER TABLE binding_groups ADD COLUMN icon_id TEXT NOT NULL DEFAULT 'folder'"
            )

        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_sessions_started_at ON activity_sessions(started_at)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_activity_sessions_activity_key ON activity_sessions(activity_key)"
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_browser_session_details_activity_session_id
            ON browser_session_details(activity_session_id)
            """
        )
        self.connection.execute(
            """
            UPDATE activity_sessions
            SET activity_key = ?, display_name = ?
            WHERE activity_key = ?
                OR display_name = ?
            """,
            (WINDOWS_OPERATION, WINDOWS_OPERATION, FILE_EXPLORER, FILE_EXPLORER),
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO ignored_activity_rules (activity_key, display_name, created_at)
            SELECT ?, ?, created_at
            FROM ignored_activity_rules
            WHERE activity_key = ?
            """,
            (WINDOWS_OPERATION, WINDOWS_OPERATION, FILE_EXPLORER),
        )
        self.connection.execute(
            "DELETE FROM ignored_activity_rules WHERE activity_key = ?",
            (FILE_EXPLORER,),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def ignore_activity(self, activity_key: str, display_name: str) -> None:
        activity_key = normalize_activity_key(activity_key)
        display_name = normalize_activity_display_name(display_name)
        self.connection.execute(
            """
            INSERT INTO ignored_activity_rules (activity_key, display_name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(activity_key) DO UPDATE SET
                display_name = excluded.display_name
            """,
            (activity_key, display_name, now_ms()),
        )
        self.connection.commit()

    def unignore_activity(self, activity_key: str) -> None:
        activity_key = normalize_activity_key(activity_key)
        self.connection.execute(
            "DELETE FROM ignored_activity_rules WHERE activity_key = ?",
            (activity_key,),
        )
        self.connection.commit()

    def delete_activity_between(self, activity_key: str, start_ms: int, end_ms: int) -> int:
        activity_key = normalize_activity_key(activity_key)
        self.connection.execute(
            """
            DELETE FROM browser_session_details
            WHERE activity_session_id IN (
                SELECT id
                FROM activity_sessions
                WHERE activity_key = ?
                    AND ended_at >= ?
                    AND started_at < ?
            )
            """,
            (activity_key, start_ms, end_ms),
        )
        cursor = self.connection.execute(
            """
            DELETE FROM activity_sessions
            WHERE activity_key = ?
                AND ended_at >= ?
                AND started_at < ?
            """,
            (activity_key, start_ms, end_ms),
        )
        self.connection.commit()
        return cursor.rowcount

    def get_ignored_activity_keys(self) -> set[str]:
        rows = self.connection.execute("SELECT activity_key FROM ignored_activity_rules").fetchall()
        return {row["activity_key"] for row in rows}

    def ignored_activities(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT activity_key, display_name, created_at
            FROM ignored_activity_rules
            ORDER BY created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def binding_groups(self) -> list[dict[str, str]]:
        rows = self.connection.execute(
            """
            SELECT group_id, display_name, icon_id
            FROM binding_groups
            ORDER BY created_at ASC, group_id ASC
            """
        ).fetchall()
        return [
            {
                "groupId": str(row["group_id"]),
                "displayName": str(row["display_name"]),
                "iconId": str(row["icon_id"]),
            }
            for row in rows
        ]

    def source_bindings(self) -> dict[str, str]:
        rows = self.connection.execute("SELECT source_key, group_id FROM source_bindings").fetchall()
        return {str(row["source_key"]): str(row["group_id"]) for row in rows}

    def create_binding_group(self, display_name: str) -> dict[str, str]:
        normalized_name = self._normalize_group_name(display_name)
        group_id = f"group-{uuid4().hex[:12]}"
        timestamp_ms = now_ms()
        try:
            self.connection.execute(
                """
                INSERT INTO binding_groups (group_id, display_name, icon_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, normalized_name, DEFAULT_GROUP_ICON_ID, timestamp_ms, timestamp_ms),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("A group with that name already exists.") from exc
        self.connection.commit()
        return {"groupId": group_id, "displayName": normalized_name, "iconId": DEFAULT_GROUP_ICON_ID}

    def rename_binding_group(self, group_id: str, display_name: str) -> dict[str, str]:
        normalized_id = self._normalize_group_id(group_id)
        normalized_name = self._normalize_group_name(display_name)
        try:
            cursor = self.connection.execute(
                """
                UPDATE binding_groups
                SET display_name = ?, updated_at = ?
                WHERE group_id = ?
                """,
                (normalized_name, now_ms(), normalized_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("A group with that name already exists.") from exc
        if cursor.rowcount != 1:
            raise ValueError("Binding group was not found.")
        self.connection.commit()
        row = self.connection.execute(
            "SELECT icon_id FROM binding_groups WHERE group_id = ?",
            (normalized_id,),
        ).fetchone()
        return {
            "groupId": normalized_id,
            "displayName": normalized_name,
            "iconId": str(row["icon_id"]),
        }

    def set_binding_group_icon(self, group_id: str, icon_id: str) -> dict[str, str]:
        normalized_id = self._normalize_group_id(group_id)
        normalized_icon_id = self._normalize_group_icon_id(icon_id)
        cursor = self.connection.execute(
            """
            UPDATE binding_groups
            SET icon_id = ?, updated_at = ?
            WHERE group_id = ?
            """,
            (normalized_icon_id, now_ms(), normalized_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Binding group was not found.")
        self.connection.commit()
        row = self.connection.execute(
            "SELECT display_name FROM binding_groups WHERE group_id = ?",
            (normalized_id,),
        ).fetchone()
        return {
            "groupId": normalized_id,
            "displayName": str(row["display_name"]),
            "iconId": normalized_icon_id,
        }

    def delete_binding_group(self, group_id: str) -> None:
        normalized_id = self._normalize_group_id(group_id)
        self.connection.execute("DELETE FROM source_bindings WHERE group_id = ?", (normalized_id,))
        cursor = self.connection.execute("DELETE FROM binding_groups WHERE group_id = ?", (normalized_id,))
        if cursor.rowcount != 1:
            raise ValueError("Binding group was not found.")
        self.connection.commit()

    def bind_source(self, group_id: str, source_key: str) -> str:
        normalized_id = self._normalize_group_id(group_id)
        normalized_source_key = self._normalize_binding_source_key(source_key)
        exists = self.connection.execute(
            "SELECT 1 FROM binding_groups WHERE group_id = ?",
            (normalized_id,),
        ).fetchone()
        if exists is None:
            raise ValueError("Binding group was not found.")

        self.connection.execute(
            """
            INSERT INTO source_bindings (source_key, group_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET group_id = excluded.group_id
            """,
            (normalized_source_key, normalized_id, now_ms()),
        )
        self.connection.commit()
        return normalized_source_key

    def unbind_source(self, source_key: str) -> str:
        normalized_source_key = self._normalize_binding_source_key(source_key)
        self.connection.execute("DELETE FROM source_bindings WHERE source_key = ?", (normalized_source_key,))
        self.connection.commit()
        return normalized_source_key

    def ignore_browser_detail(self, source_key: str, display_name: str) -> str:
        normalized_source_key = self._normalize_browser_source_key(source_key)
        normalized_display_name = display_name.strip() or normalized_source_key
        self.connection.execute(
            """
            INSERT INTO ignored_browser_detail_rules (source_key, display_name, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET display_name = excluded.display_name
            """,
            (normalized_source_key, normalized_display_name, now_ms()),
        )
        self.connection.commit()
        self._ignored_browser_source_keys.add(normalized_source_key)
        return normalized_source_key

    def ignored_browser_detail_source_keys(self) -> set[str]:
        return set(self._ignored_browser_source_keys)

    def _refresh_ignored_browser_source_keys(self) -> None:
        rows = self.connection.execute("SELECT source_key FROM ignored_browser_detail_rules").fetchall()
        self._ignored_browser_source_keys = {str(row["source_key"]) for row in rows}

    def ignored_browser_details(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT source_key, display_name, created_at
            FROM ignored_browser_detail_rules
            ORDER BY created_at DESC
            """
        ).fetchall()
        return [
            {
                "activity_key": str(row["source_key"]),
                "display_name": f"{row['display_name']} - Chrome",
                "created_at": int(row["created_at"]),
                "sourceType": "browser",
                "source_type": "browser",
            }
            for row in rows
        ]

    def unignore_browser_detail(self, source_key: str) -> str:
        normalized_source_key = self._normalize_browser_source_key(source_key)
        self.connection.execute(
            "DELETE FROM ignored_browser_detail_rules WHERE source_key = ?",
            (normalized_source_key,),
        )
        self.connection.commit()
        self._ignored_browser_source_keys.discard(normalized_source_key)
        return normalized_source_key

    def is_browser_detail_ignored(self, browser_detail: dict[str, Any] | None) -> bool:
        return bool(
            browser_detail
            and browser_detail_key(browser_detail) in self.ignored_browser_detail_source_keys()
        )

    def delete_browser_detail_between(self, source_key: str, start_ms: int, end_ms: int) -> int:
        normalized_source_key = self._normalize_browser_source_key(source_key)
        rows = self.connection.execute(
            """
            SELECT
                activity_sessions.id AS activity_session_id,
                browser_session_details.url,
                browser_session_details.host,
                browser_session_details.tracking_status
            FROM activity_sessions
            JOIN browser_session_details
                ON browser_session_details.activity_session_id = activity_sessions.id
            WHERE activity_sessions.ended_at >= ?
                AND activity_sessions.started_at < ?
            """,
            (start_ms, end_ms),
        ).fetchall()
        session_ids = [
            int(row["activity_session_id"])
            for row in rows
            if browser_detail_key(
                {
                    "host": raw_browser_host(str(row["url"] or "")) or str(row["host"] or ""),
                    "tracking_status": str(row["tracking_status"] or "other"),
                }
            )
            == normalized_source_key
        ]
        if not session_ids:
            return 0

        placeholders = ", ".join("?" for _ in session_ids)
        self.connection.execute(
            f"DELETE FROM browser_session_details WHERE activity_session_id IN ({placeholders})",
            session_ids,
        )
        cursor = self.connection.execute(
            f"DELETE FROM activity_sessions WHERE id IN ({placeholders})",
            session_ids,
        )
        self.connection.commit()
        return cursor.rowcount

    def is_activity_ignored(self, focus: FocusInfo) -> bool:
        return self.get_activity_key(focus) in self.get_ignored_activity_keys()

    def insert_session(
        self,
        focus: FocusInfo,
        started_at: int,
        ended_at: int,
        browser_detail: dict[str, Any] | None = None,
    ) -> None:
        duration_ms = max(0, ended_at - started_at)
        if duration_ms <= 0:
            return

        cursor = self.connection.execute(
            """
            INSERT INTO activity_sessions (
                started_at,
                ended_at,
                duration_ms,
                activity_key,
                display_name,
                process_name,
                process_path,
                window_class,
                window_title,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                ended_at,
                duration_ms,
                self.get_activity_key(focus),
                normalize_activity_display_name(focus.display_name),
                focus.process_name,
                focus.process_path,
                focus.window_class,
                focus.window_title,
                now_ms(),
            ),
        )
        activity_session_id = int(cursor.lastrowid)
        if browser_detail is not None:
            self.connection.execute(
                """
                INSERT INTO browser_session_details (
                    activity_session_id,
                    browser_name,
                    url,
                    host,
                    title,
                    tracking_status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity_session_id,
                    str(browser_detail.get("browser_name") or focus.process_name),
                    str(browser_detail.get("url") or ""),
                    str(browser_detail.get("host") or ""),
                    str(browser_detail.get("title") or focus.window_title),
                    str(browser_detail.get("tracking_status") or "other"),
                    now_ms(),
                ),
            )
        self.connection.commit()

    def summary_since(self, since_ms: int) -> dict[str, dict[str, Any]]:
        return self.summary_between(since_ms, None)

    def summary_between(self, start_ms: int, end_ms: int | None = None) -> dict[str, dict[str, Any]]:
        if end_ms is None:
            overlap_expression = "CASE WHEN started_at < ? THEN ended_at - ? ELSE duration_ms END"
            range_filter = "ended_at >= ?"
            params = (start_ms, start_ms, start_ms)
        else:
            overlap_expression = "MAX(0, MIN(ended_at, ?) - MAX(started_at, ?))"
            range_filter = "ended_at >= ? AND started_at < ?"
            params = (end_ms, start_ms, start_ms, end_ms)

        rows = self.connection.execute(
            f"""
            WITH filtered_sessions AS (
                SELECT
                    *,
                    {overlap_expression} AS overlap_ms
                FROM activity_sessions
                WHERE {range_filter}
                    AND activity_key NOT IN (
                        SELECT activity_key
                        FROM ignored_activity_rules
                    )
            ),
            activity_totals AS (
                SELECT
                    activity_key,
                    SUM(overlap_ms) AS total_ms
                FROM filtered_sessions
                GROUP BY activity_key
            ),
            representative_sessions AS (
                SELECT *
                FROM (
                    SELECT
                        *,
                        ROW_NUMBER() OVER (
                            PARTITION BY activity_key
                            ORDER BY
                                CASE WHEN process_path != '' THEN 0 ELSE 1 END,
                                ended_at DESC,
                                id DESC
                        ) AS row_number
                    FROM filtered_sessions
                )
                WHERE row_number = 1
            )
            SELECT
                activity_totals.activity_key,
                representative_sessions.display_name,
                representative_sessions.process_name,
                representative_sessions.process_path,
                representative_sessions.window_class,
                representative_sessions.window_title,
                activity_totals.total_ms
            FROM activity_totals
            JOIN representative_sessions
                ON representative_sessions.activity_key = activity_totals.activity_key
            ORDER BY activity_totals.total_ms DESC
            """,
            params,
        ).fetchall()

        totals = {
            row["activity_key"]: {
                "activityKey": row["activity_key"],
                "displayName": row["display_name"],
                "totalMs": int(row["total_ms"] or 0),
                "focus": {
                    "hwnd": 0,
                    "pid": 0,
                    "process_name": row["process_name"],
                    "process_path": row["process_path"],
                    "window_class": row["window_class"],
                    "window_title": row["window_title"],
                    "display_name": row["display_name"],
                },
            }
            for row in rows
        }
        self._add_browser_details_to_totals(totals, start_ms, end_ms)
        return totals

    def _normalize_group_name(self, display_name: str) -> str:
        normalized = display_name.strip()
        if not normalized:
            raise ValueError("Group name is required.")
        if len(normalized) > 80:
            raise ValueError("Group name must be 80 characters or fewer.")
        return normalized

    def _normalize_group_id(self, group_id: str) -> str:
        normalized = group_id.strip()
        if not normalized.startswith("group-"):
            raise ValueError("Invalid binding group id.")
        return normalized

    def _normalize_group_icon_id(self, icon_id: str) -> str:
        normalized = icon_id.strip().lower()
        if not GROUP_ICON_ID_RE.fullmatch(normalized):
            raise ValueError("Invalid group icon id.")
        return normalized

    def _normalize_binding_source_key(self, source_key: str) -> str:
        normalized = source_key.strip().lower()
        if not normalized:
            raise ValueError("Source key is required.")
        if normalized == "browser:other":
            raise ValueError("Other browser activity cannot be bound.")
        if normalized.startswith("browser:"):
            host = normalize_browser_host(normalized.removeprefix("browser:"))
            if not host:
                raise ValueError("Invalid browser source key.")
            return f"browser:{host}"
        if normalized.startswith("group:"):
            raise ValueError("User groups cannot be bound as sources.")
        if is_supported_browser(normalized):
            raise ValueError("Browser parent activity cannot be bound.")
        return normalize_activity_key(normalized).lower()

    def _normalize_browser_source_key(self, source_key: str) -> str:
        normalized = source_key.strip().lower()
        if not normalized.startswith("browser:"):
            raise ValueError("Browser source key is required.")
        host = normalize_browser_host(normalized.removeprefix("browser:"))
        if not host:
            raise ValueError("Invalid browser source key.")
        normalized_source_key = f"browser:{host}"
        if normalized_source_key == "browser:other":
            raise ValueError("Other browser activity cannot be changed.")
        return normalized_source_key

    def _add_browser_details_to_totals(
        self,
        totals: dict[str, dict[str, Any]],
        start_ms: int,
        end_ms: int | None,
    ) -> None:
        browser_items = [
            item
            for item in totals.values()
            if is_supported_browser(str(item.get("focus", {}).get("process_name") or ""))
        ]
        if not browser_items:
            return

        if end_ms is None:
            overlap_expression = "CASE WHEN activity_sessions.started_at < ? THEN activity_sessions.ended_at - ? ELSE activity_sessions.duration_ms END"
            range_filter = "activity_sessions.ended_at >= ?"
            params: tuple[Any, ...] = (start_ms, start_ms, start_ms)
        else:
            overlap_expression = "MAX(0, MIN(activity_sessions.ended_at, ?) - MAX(activity_sessions.started_at, ?))"
            range_filter = "activity_sessions.ended_at >= ? AND activity_sessions.started_at < ?"
            params = (end_ms, start_ms, start_ms, end_ms)

        rows = self.connection.execute(
            f"""
            SELECT
                activity_sessions.activity_key,
                activity_sessions.process_name,
                browser_session_details.host,
                browser_session_details.url,
                browser_session_details.tracking_status,
                {overlap_expression} AS overlap_ms
            FROM activity_sessions
            LEFT JOIN browser_session_details
                ON browser_session_details.activity_session_id = activity_sessions.id
            WHERE {range_filter}
                AND activity_sessions.activity_key NOT IN (
                    SELECT activity_key
                    FROM ignored_activity_rules
                )
            """,
            params,
        ).fetchall()

        details_by_activity_key: dict[str, dict[str, dict[str, Any]]] = {}
        ignored_total_by_activity_key: dict[str, int] = {}
        ignored_sources = self.ignored_browser_detail_source_keys()
        for row in rows:
            if not is_supported_browser(str(row["process_name"] or "")):
                continue

            overlap_ms = int(row["overlap_ms"] or 0)
            if overlap_ms <= 0:
                continue

            host = raw_browser_host(str(row["url"] or "")) or str(row["host"] or "")
            tracking_status = str(row["tracking_status"] or "other")
            normalized_label = site_display_name(host, tracking_status)
            normalized_host = normalize_browser_host(host)
            detail_key = "other" if normalized_label == OTHER_SITE_LABEL else normalized_host
            activity_key = str(row["activity_key"])
            source_key = f"browser:{detail_key}"
            if source_key in ignored_sources:
                ignored_total_by_activity_key[activity_key] = (
                    ignored_total_by_activity_key.get(activity_key, 0) + overlap_ms
                )
                continue
            details = details_by_activity_key.setdefault(activity_key, {})
            detail = details.setdefault(
                detail_key,
                {
                    "key": f"browser:{detail_key}",
                    "label": normalized_label,
                    "host": normalized_host if normalized_label != OTHER_SITE_LABEL else "",
                    "faviconUrl": favicon_url(host, tracking_status if normalized_label != OTHER_SITE_LABEL else "other"),
                    "trackingStatus": tracking_status if normalized_label != OTHER_SITE_LABEL else "other",
                    "totalMs": 0,
                },
            )
            detail["totalMs"] += overlap_ms

        for item in browser_items:
            activity_key = str(item["activityKey"])
            details = list(details_by_activity_key.get(activity_key, {}).values())
            details.sort(key=lambda detail: int(detail["totalMs"]), reverse=True)
            item["browserDetails"] = details
            item["totalMs"] = max(
                0,
                int(item.get("totalMs") or 0) - ignored_total_by_activity_key.get(activity_key, 0),
            )

    def recent_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM activity_sessions
            ORDER BY ended_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [dict(row) for row in rows]

    def recent_browser_details(self, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
                activity_sessions.id AS session_id,
                activity_sessions.started_at,
                activity_sessions.ended_at,
                activity_sessions.duration_ms,
                activity_sessions.process_name,
                activity_sessions.window_title,
                browser_session_details.id AS detail_id,
                browser_session_details.url,
                browser_session_details.host,
                browser_session_details.title,
                browser_session_details.tracking_status
            FROM activity_sessions
            JOIN browser_session_details
                ON browser_session_details.activity_session_id = activity_sessions.id
            ORDER BY activity_sessions.ended_at DESC, activity_sessions.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [dict(row) for row in rows]

    def get_activity_key(self, focus: FocusInfo) -> str:
        return normalize_activity_display_name(focus.display_name).lower()


def serialize_focus(focus: FocusInfo | None) -> dict[str, Any] | None:
    if focus is None:
        return None

    return asdict(focus)
