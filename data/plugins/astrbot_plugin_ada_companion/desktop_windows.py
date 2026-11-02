"""Observe real windows and recover a refused activation using a verified caption."""

import ctypes
from ctypes import wintypes

from .desktop_guard import DesktopError


def window_inventory():
    """Observe visible, non-cloaked top-level windows without changing focus."""
    user, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    dwm.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(handle, _):
        if not user.IsWindowVisible(handle):
            return True
        title, rect, cloaked = (
            ctypes.create_unicode_buffer(512),
            wintypes.RECT(),
            wintypes.DWORD(),
        )
        user.GetWindowTextW(handle, title, 512)
        if (
            title.value
            and not title.value.startswith("windows-mcp-")
            and title.value != "Program Manager"
            and dwm.DwmGetWindowAttribute(
                handle, 9, ctypes.byref(rect), ctypes.sizeof(rect)
            )
            == 0
        ):
            dwm.DwmGetWindowAttribute(
                handle, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )
            if (
                not cloaked.value
                and rect.right - rect.left > 80
                and rect.bottom - rect.top > 60
            ):
                found.append(
                    {
                        "handle": int(handle),
                        "title": title.value,
                        "bounds": [rect.left, rect.top, rect.right, rect.bottom],
                    }
                )
        return True

    user.EnumWindows(callback_type(visit), 0)
    return found[:40]


def caption_point(name):
    """Expose one requested window and verify its title bar before activation.

    Args:
        name: Exact, observed window title.

    Returns:
        Window identity and an unobstructed physical title-bar point.

    Raises:
        DesktopError: The target is ambiguous, unavailable or still covered.
    """
    candidates = [
        w for w in window_inventory() if w["title"].casefold() == name.casefold()
    ]
    if len(candidates) != 1:
        raise DesktopError(
            "Cannot safely activate this window; use its exact visible title."
        )
    target = candidates[0]
    user = ctypes.windll.user32
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    # Windows can refuse foreground activation while allowing z-order changes.
    # Expose only this requested window, without moving/resizing it, then retain
    # its original topmost state. A verified caption click still owns activation.
    was_topmost = bool(user.GetWindowLongW(target["handle"], -20) & 8)
    try:
        if not user.SetWindowPos(target["handle"], -1, 0, 0, 0, 0, 0x13):
            raise DesktopError("Could not expose the requested window.")
    finally:
        if not was_topmost:
            user.SetWindowPos(target["handle"], -2, 0, 0, 0, 0, 0x13)
    user.WindowFromPhysicalPoint.restype = wintypes.HWND
    user.WindowFromPhysicalPoint.argtypes = [wintypes.POINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    left, top, right, _ = target["bounds"]
    for x in ((left + right) // 2, left + 70):
        y = top + 15
        handle = user.WindowFromPhysicalPoint(wintypes.POINT(x, y))
        if int(user.GetAncestor(handle, 2) or 0) != target["handle"]:
            continue
        hit = ctypes.c_size_t()
        packed = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
        ok = user.SendMessageTimeoutW(
            target["handle"], 0x84, 0, packed, 2, 500, ctypes.byref(hit)
        )
        if ok and hit.value == 2:
            return target, [x, y]
    raise DesktopError(
        "No unobstructed title bar was verified. Window was not clicked."
    )
