from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import re
from typing import Any
from urllib.parse import quote, urlparse

from focus_watcher import FocusInfo


BROWSER_PROCESS_NAMES = {
    "chrome.exe",
}

OTHER_SITE_LABEL = "Other"
PRIVATE_BROWSER_STATUS = "private"
UNKNOWN_BROWSER_STATUS = "unknown"
NORMAL_BROWSER_STATUS = "normal"

# Chrome exposes this label through UIAutomation on its profile/incognito button.
# Matching the mode label, rather than a page title or URL, keeps private-page
# content out of the detection path. The compact list covers Chrome's common UI
# languages; an unrecognised UI state is handled conservatively as unreadable.
PRIVATE_MODE_LABEL_TOKENS = (
    "incognito",
    "inprivate",
    "private browsing",
    "private mode",
    "시크릿",
    "비공개",
    "privat",
    "navegacion privada",
    "navegação privada",
    "navigation privee",
    "navigazione in incognito",
    "modo privado",
    "modo anonimo",
    "gizli mod",
    "режим инкогнито",
    "隐身",
    "無痕",
    "プライベート",
)

SITE_LABELS = {
    "chatgpt.com": "ChatGPT",
    "gemini.google.com": "Gemini",
    "youtube.com": "YouTube",
    "music.youtube.com": "YouTube Music",
    "naver.com": "Naver",
    "facebook.com": "Facebook",
    "github.com": "GitHub",
    "docs.google.com": "Google Docs",
    "grok.com": "Grok",
    "inflearn.com": "Inflearn",
}

LOCAL_BROWSER_HOSTS = {
    "127.0.0.1",
    "localhost",
}

MULTI_PART_PUBLIC_SUFFIXES = {
    "ac.kr",
    "co.jp",
    "co.kr",
    "co.uk",
    "com.au",
    "go.kr",
    "or.kr",
}

IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


@dataclass(frozen=True)
class BrowserDetail:
    browser_name: str
    url: str
    host: str
    title: str
    tracking_status: str
    privacy_mode: str = NORMAL_BROWSER_STATUS


def is_supported_browser(process_name: str) -> bool:
    return process_name.lower() in BROWSER_PROCESS_NAMES


def read_browser_detail(
    focus: FocusInfo,
    privacy_mode: str | None = None,
) -> BrowserDetail | None:
    if not is_supported_browser(focus.process_name):
        return None

    resolved_privacy_mode = privacy_mode or chrome_privacy_mode(focus)
    if resolved_privacy_mode != NORMAL_BROWSER_STATUS:
        return _private_browser_detail(focus, resolved_privacy_mode)

    reader = ChromeUrlReader()
    url = reader.read_url(focus.hwnd)
    host = raw_browser_host(url)
    status = browser_tracking_status(host)
    return BrowserDetail(
        browser_name=focus.process_name,
        url=url or "",
        host=host,
        title=focus.window_title,
        tracking_status=status,
        privacy_mode=resolved_privacy_mode,
    )


def browser_detail_from_url(
    focus: FocusInfo,
    url: str,
    privacy_mode: str | None = None,
) -> BrowserDetail | None:
    if not is_supported_browser(focus.process_name):
        return None

    resolved_privacy_mode = privacy_mode or chrome_privacy_mode(focus)
    if resolved_privacy_mode != NORMAL_BROWSER_STATUS:
        return _private_browser_detail(focus, resolved_privacy_mode)

    normalized_url = normalize_url(url)
    host = raw_browser_host(normalized_url)
    status = browser_tracking_status(host)
    return BrowserDetail(
        browser_name=focus.process_name,
        url=normalized_url or "",
        host=host,
        title=focus.window_title,
        tracking_status=status,
        privacy_mode=resolved_privacy_mode,
    )


def _private_browser_detail(focus: FocusInfo, privacy_mode: str) -> BrowserDetail:
    return BrowserDetail(
        browser_name=focus.process_name,
        url="",
        host="",
        title="",
        tracking_status="other",
        privacy_mode=privacy_mode,
    )


def chrome_privacy_mode(focus: FocusInfo) -> str:
    if not is_supported_browser(focus.process_name):
        return UNKNOWN_BROWSER_STATUS

    return ChromeWindowInspector().privacy_mode(focus.hwnd)


def browser_detail_key(detail: dict[str, Any] | BrowserDetail | None) -> str:
    if detail is None:
        return ""

    if isinstance(detail, BrowserDetail):
        host = detail.host
        status = detail.tracking_status
    else:
        host = str(detail.get("host") or "")
        status = str(detail.get("tracking_status") or "")

    normalized_host = normalize_browser_host(host)
    return f"browser:{normalized_host}" if status == "tracked" and normalized_host else "browser:other"


class ChromeUrlReader:
    def read_url(self, hwnd: int) -> str:
        try:
            from pywinauto import Desktop
        except ImportError:
            return ""

        try:
            window = Desktop(backend="uia").window(handle=hwnd)
            edits = window.descendants(control_type="Edit")
        except Exception:
            return ""

        for edit in edits:
            for value in self._candidate_values(edit):
                normalized = normalize_url(value)
                if normalized:
                    return normalized

        return ""

    def _candidate_values(self, edit: Any) -> list[str]:
        values: list[str] = []
        for attr_name in ("get_value", "window_text"):
            attr = getattr(edit, attr_name, None)
            if not callable(attr):
                continue
            try:
                value = attr()
            except Exception:
                continue
            if isinstance(value, str):
                values.append(value)

        element_info = getattr(edit, "element_info", None)
        for attr_name in ("name", "rich_text"):
            value = getattr(element_info, attr_name, None)
            if isinstance(value, str):
                values.append(value)

        iface_value = getattr(edit, "iface_value", None)
        try:
            value = iface_value.CurrentValue if iface_value else None
        except Exception:
            value = None
        if isinstance(value, str):
            values.append(value)

        return values

    def find_address_bar_element(self, hwnd: int) -> Any | None:
        try:
            from pywinauto import Desktop
        except ImportError:
            return None

        try:
            window = Desktop(backend="uia").window(handle=hwnd)
            edits = window.descendants(control_type="Edit")
        except Exception:
            return None

        for edit in edits:
            for value in self._candidate_values(edit):
                if normalize_url(value):
                    return getattr(edit.element_info, "element", None)

        return None


class ChromeWindowInspector:
    """Reads Chrome's own UI state without touching the address bar value."""

    def privacy_mode(self, hwnd: int) -> str:
        try:
            from pywinauto import Desktop
        except ImportError:
            return UNKNOWN_BROWSER_STATUS

        try:
            window = Desktop(backend="uia").window(handle=hwnd)
            profile_buttons = [
                button
                for button in window.descendants(control_type="Button")
                if getattr(button.element_info, "class_name", "") == "AvatarToolbarButton"
            ]
        except Exception:
            return UNKNOWN_BROWSER_STATUS

        if not profile_buttons:
            return UNKNOWN_BROWSER_STATUS

        for button in profile_buttons:
            name = str(getattr(button.element_info, "name", "") or "").casefold()
            if any(token in name for token in PRIVATE_MODE_LABEL_TOKENS):
                return PRIVATE_BROWSER_STATUS

        return NORMAL_BROWSER_STATUS


class ChromeAddressBarChangeHandler:
    def __init__(self, focus: FocusInfo, on_change) -> None:
        import comtypes
        import comtypes.gen.UIAutomationClient as UIA

        class Handler(comtypes.COMObject):
            _com_interfaces_ = [UIA.IUIAutomationPropertyChangedEventHandler]

            def HandlePropertyChangedEvent(self, sender, property_id, new_value):
                if property_id == UIA.UIA_ValueValuePropertyId:
                    try:
                        if sender.CurrentHasKeyboardFocus:
                            return 0
                    except Exception:
                        pass

                    value = getattr(new_value, "value", new_value)
                    if isinstance(value, str):
                        on_change(focus, value)
                return 0

        self.handler = Handler()


class ChromeAddressBarEventSubscription:
    def __init__(self, focus: FocusInfo, on_change, privacy_mode: str | None = None) -> None:
        self.focus = focus
        self.on_change = on_change
        self.privacy_mode = privacy_mode
        self.uia = None
        self.element = None
        self.handler_wrapper: ChromeAddressBarChangeHandler | None = None
        self.property_ids = None
        self.active = False

    def start(self) -> bool:
        if not is_supported_browser(self.focus.process_name):
            return False
        resolved_privacy_mode = self.privacy_mode or chrome_privacy_mode(self.focus)
        if resolved_privacy_mode != NORMAL_BROWSER_STATUS:
            return False

        try:
            import comtypes.gen.UIAutomationClient as UIA
            from pywinauto.uia_defines import IUIA
        except Exception:
            return False

        element = ChromeUrlReader().find_address_bar_element(self.focus.hwnd)
        if element is None:
            return False

        self.uia = IUIA().iuia
        self.element = element
        self.handler_wrapper = ChromeAddressBarChangeHandler(self.focus, self.on_change)
        self.property_ids = (ctypes.c_long * 1)(UIA.UIA_ValueValuePropertyId)

        try:
            self.uia.AddPropertyChangedEventHandlerNativeArray(
                self.element,
                UIA.TreeScope_Element,
                None,
                self.handler_wrapper.handler,
                self.property_ids,
                1,
            )
        except Exception:
            self.uia = None
            self.element = None
            self.handler_wrapper = None
            self.property_ids = None
            return False

        self.active = True
        return True

    def stop(self) -> None:
        if not self.active or self.uia is None or self.element is None or self.handler_wrapper is None:
            self.active = False
            return

        try:
            self.uia.RemovePropertyChangedEventHandler(self.element, self.handler_wrapper.handler)
        except Exception:
            pass
        finally:
            self.active = False
            self.uia = None
            self.element = None
            self.handler_wrapper = None
            self.property_ids = None


def normalize_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""

    lowered = candidate.lower()
    if lowered in {"about:blank", "new tab", "search or type web address"}:
        return ""
    if " " in candidate and "://" not in candidate:
        return ""

    parse_target = candidate
    if "://" not in parse_target:
        parse_target = f"https://{parse_target}"

    parsed = urlparse(parse_target)
    host = parsed.hostname or ""
    if not _looks_like_host(host):
        return ""

    return parse_target


def normalize_browser_host(url_or_host: str) -> str:
    value = url_or_host.strip()
    if not value:
        return ""

    parse_target = value if "://" in value else f"https://{value}"
    parsed = urlparse(parse_target)
    host = (parsed.hostname or "").lower().strip(".")
    if not _looks_like_host(host):
        return ""

    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    if IPV4_RE.match(host):
        return host

    for known_host in sorted(SITE_LABELS, key=len, reverse=True):
        if host == known_host or host.endswith(f".{known_host}"):
            return known_host

    labels = host.split(".")
    if len(labels) <= 2:
        return host

    suffix = ".".join(labels[-2:])
    if suffix in MULTI_PART_PUBLIC_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])

    return suffix


def raw_browser_host(url_or_host: str) -> str:
    value = url_or_host.strip()
    if not value:
        return ""

    parse_target = value if "://" in value else f"https://{value}"
    parsed = urlparse(parse_target)
    host = (parsed.hostname or "").lower().strip(".")
    return host if _looks_like_host(host) else ""


def browser_tracking_status(host: str) -> str:
    return "tracked" if host and not is_local_browser_host(host) else "other"


def is_local_browser_host(host: str) -> bool:
    return normalize_browser_host(host) in LOCAL_BROWSER_HOSTS


def site_display_name(host: str, tracking_status: str = "tracked") -> str:
    normalized_host = normalize_browser_host(host)
    if tracking_status != "tracked" or not normalized_host or normalized_host in LOCAL_BROWSER_HOSTS:
        return OTHER_SITE_LABEL

    if normalized_host in SITE_LABELS:
        return SITE_LABELS[normalized_host]

    name = normalized_host.split(".", 1)[0].replace("-", " ").replace("_", " ").strip()
    return name.title() if name else OTHER_SITE_LABEL


def favicon_url(host: str, tracking_status: str = "tracked") -> str:
    normalized_host = normalize_browser_host(host)
    if tracking_status != "tracked" or not normalized_host or normalized_host in LOCAL_BROWSER_HOSTS:
        return ""

    return f"https://www.google.com/s2/favicons?domain={quote(normalized_host)}&sz=64"


def serialize_browser_detail(detail: BrowserDetail | None) -> dict[str, Any] | None:
    if detail is None:
        return None

    return asdict(detail)


def _looks_like_host(value: str) -> bool:
    if not value:
        return False
    if value in {"localhost"}:
        return True
    if IPV4_RE.match(value):
        parts = value.split(".")
        return all(0 <= int(part) <= 255 for part in parts)
    if "." not in value:
        return False
    if all(part.isdigit() for part in value.split(".")):
        return False
    return all(part for part in value.split("."))
