from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import win32con
import win32gui
import win32ui
import psutil
from PIL import Image


ICON_SIZE = 48


class IconProvider:
    def __init__(self) -> None:
        self.cache: dict[str, str | None] = {}
        self.process_name_cache: dict[str, str | None] = {}

    def get_icon_data_url(self, process_path: str | None, process_name: str | None = None) -> str | None:
        resolved_path = process_path
        if resolved_path:
            icon_data_url = self.get_icon_data_url_from_path(resolved_path)
            if icon_data_url:
                return icon_data_url

        fallback_path = self.resolve_process_path(process_name)
        if not fallback_path or fallback_path == resolved_path:
            return None

        return self.get_icon_data_url_from_path(fallback_path)

    def get_icon_data_url_from_path(self, process_path: str) -> str | None:
        normalized_path = str(Path(process_path)).lower()
        if normalized_path in self.cache:
            return self.cache[normalized_path]

        icon_data_url = extract_icon_data_url(process_path)
        self.cache[normalized_path] = icon_data_url
        return icon_data_url

    def resolve_process_path(self, process_name: str | None) -> str | None:
        if not process_name:
            return None

        normalized_name = process_name.lower()
        if normalized_name in self.process_name_cache:
            return self.process_name_cache[normalized_name]

        resolved_path = find_running_process_path(normalized_name)
        self.process_name_cache[normalized_name] = resolved_path
        return resolved_path


def find_running_process_path(normalized_process_name: str) -> str | None:
    try:
        processes = psutil.process_iter(["name", "exe"])
    except Exception:
        return None

    for process in processes:
        try:
            name = process.info.get("name")
            if not name or name.lower() != normalized_process_name:
                continue

            exe_path = process.info.get("exe") or process.exe()
            if exe_path and Path(exe_path).exists():
                return exe_path
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue

    return None


def extract_icon_data_url(process_path: str) -> str | None:
    if not Path(process_path).exists():
        return None

    large_icons: list[int] = []
    small_icons: list[int] = []
    hicon = None

    try:
        large_icons, small_icons = win32gui.ExtractIconEx(process_path, 0, 1)
        hicon = (large_icons or small_icons or [None])[0]
        if not hicon:
            return None

        image = hicon_to_image(hicon, ICON_SIZE)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return None
    finally:
        for icon_handle in [*small_icons, *large_icons]:
            if icon_handle:
                try:
                    win32gui.DestroyIcon(icon_handle)
                except Exception:
                    pass


def hicon_to_image(hicon: int, size: int) -> Image.Image:
    screen_hdc = win32gui.GetDC(0)
    screen_dc = win32ui.CreateDCFromHandle(screen_hdc)
    memory_dc = screen_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(screen_dc, size, size)
    previous_bitmap = memory_dc.SelectObject(bitmap)

    try:
        win32gui.DrawIconEx(memory_dc.GetSafeHdc(), 0, 0, hicon, size, size, 0, None, win32con.DI_NORMAL)
        bitmap_bits = bitmap.GetBitmapBits(True)
        return Image.frombuffer("RGBA", (size, size), bitmap_bits, "raw", "BGRA", 0, 1).copy()
    finally:
        memory_dc.SelectObject(previous_bitmap)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        screen_dc.DeleteDC()
        win32gui.ReleaseDC(0, screen_hdc)
