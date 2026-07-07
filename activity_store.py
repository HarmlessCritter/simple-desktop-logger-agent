from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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

    def insert_session(self, focus: FocusInfo, started_at: int, ended_at: int) -> None:
        duration_ms = max(0, ended_at - started_at)
        if duration_ms <= 0:
            return

        self.connection.execute(
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

        return {
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

    def get_activity_key(self, focus: FocusInfo) -> str:
        return normalize_activity_display_name(focus.display_name).lower()


def serialize_focus(focus: FocusInfo | None) -> dict[str, Any] | None:
    if focus is None:
        return None

    return asdict(focus)
