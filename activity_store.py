from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from browser_tracking import (
    OTHER_SITE_LABEL,
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
        self._initialize()

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
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(activity_sessions)").fetchall()
        }
        if "process_path" not in columns:
            self.connection.execute("ALTER TABLE activity_sessions ADD COLUMN process_path TEXT NOT NULL DEFAULT ''")

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
            row["display_name"]: {
                "activityKey": row["activity_key"],
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
            details = list(details_by_activity_key.get(str(item["activityKey"]), {}).values())
            details.sort(key=lambda detail: int(detail["totalMs"]), reverse=True)
            item["browserDetails"] = details

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
