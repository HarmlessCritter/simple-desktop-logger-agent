from __future__ import annotations

import json
from pathlib import Path
from typing import Any


APP_DATA_DIR = Path.home() / "AppData" / "Local" / "SimpleDesktopLogger"
SETTINGS_PATH = APP_DATA_DIR / "settings.json"
DEFAULT_LANGUAGE = "en"
LANGUAGE_LABELS = {
    "en": "English",
    "ko": "한국어",
}

TRANSLATIONS = {
    "en": {
        "menu.open_dashboard": "Open Activity History (Web)",
        "menu.info": "About",
        "menu.run_at_startup": "Run at Windows startup",
        "menu.language": "Language",
        "menu.exit": "Exit",
        "startup.title": "Run at Windows startup is off",
        "startup.message": (
            "Simple Desktop Logger has started recording.\n"
            "To start it automatically when Windows starts, enable it from the tray menu."
        ),
        "info.title": "About Simple Desktop Logger",
        "info.heading": "About Simple Desktop Logger",
        "info.safety": (
            "This program does not include ads, unauthorized data collection, grids, "
            "or other unrelated add-on features."
        ),
        "info.source_notice": "The source code is published on GitHub for anyone to review.",
        "info.source_label": "Source code repository : ",
        "info.permission": (
            "This program may be shared and distributed freely without separate permission.\n"
            "However, modifying or tampering with the program is prohibited."
        ),
        "button.ok": "OK",
        "cli.description": "Run the local Simple Desktop Logger WebSocket agent.",
        "cli.stopped": "Simple Desktop Logger agent server stopped.",
        "server.listening": "Simple Desktop Logger agent server listening on ws://{host}:{port}",
    },
    "ko": {
        "menu.open_dashboard": "활동기록 확인 (Web)",
        "menu.info": "정보",
        "menu.run_at_startup": "Windows 시작 시 자동 실행",
        "menu.language": "언어",
        "menu.exit": "종료",
        "startup.title": "Windows 시작 시 자동 실행이 꺼져 있어요",
        "startup.message": (
            "Simple Desktop Logger가 기록을 시작합니다.\n"
            "컴퓨터를 켤 때 자동으로 시작하려면 트레이 메뉴에서 설정할 수 있어요."
        ),
        "info.title": "Simple Desktop Logger 정보",
        "info.heading": "Simple Desktop Logger 정보",
        "info.safety": (
            "이 프로그램에는 광고, 무단 데이터 수집, 그리드 등 본연의 기능과 관련 없는\n"
            "부가기능이 존재하지 않습니다."
        ),
        "info.source_notice": "소스코드는 GitHub에 공개되어 누구나 확인할 수 있습니다.",
        "info.source_label": "소스코드 저장소 : ",
        "info.permission": (
            "본 프로그램은 별도 허가 없이 자유롭게 공유 및 배포할 수 있습니다.\n"
            "단, 프로그램을 수정하거나 변조하는 행위는 금지합니다."
        ),
        "button.ok": "확인",
        "cli.description": "로컬 Simple Desktop Logger WebSocket 에이전트를 실행합니다.",
        "cli.stopped": "Simple Desktop Logger 에이전트 서버가 중지되었습니다.",
        "server.listening": "Simple Desktop Logger agent server listening on ws://{host}:{port}",
    },
}


def normalize_language(language: str | None) -> str:
    if language in TRANSLATIONS:
        return language
    return DEFAULT_LANGUAGE


def load_settings() -> dict[str, Any]:
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_settings(settings: dict[str, Any]) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with SETTINGS_PATH.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


def get_language() -> str:
    return normalize_language(load_settings().get("language"))


def set_language(language: str) -> str:
    normalized = normalize_language(language)
    settings = load_settings()
    settings["language"] = normalized
    save_settings(settings)
    return normalized


def text(key: str, language: str | None = None, **kwargs: Any) -> str:
    selected = normalize_language(language or get_language())
    value = TRANSLATIONS[selected].get(key, TRANSLATIONS[DEFAULT_LANGUAGE][key])
    if kwargs:
        return value.format(**kwargs)
    return value
