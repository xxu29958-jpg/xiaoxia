"""Validate Windows input against the most recently observed foreground window."""

import ctypes
import re
import time
from ctypes import wintypes


class DesktopError(ValueError):
    """An actionable desktop failure without a success claim."""


def foreground():
    """Read window identity and bounds without activating any application."""
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    handle = user.GetForegroundWindow()
    if not handle:
        raise DesktopError("No active desktop window. Unlock Windows first.")
    title = ctypes.create_unicode_buffer(512)
    user.GetWindowTextW(handle, title, 512)
    rect = wintypes.RECT()
    # DWM frame bounds are physical pixels even when AstrBot is not DPI aware.
    dwm = ctypes.windll.dwmapi
    dwm.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    if (
        dwm.DwmGetWindowAttribute(handle, 9, ctypes.byref(rect), ctypes.sizeof(rect))
        != 0
    ):
        raise DesktopError("Cannot determine physical window bounds.")
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.OpenProcess(0x1000, False, pid.value)
    executable = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    try:
        if not process or not kernel.QueryFullProcessImageNameW(
            process, 0, executable, ctypes.byref(size)
        ):
            raise DesktopError("Cannot verify the foreground process.")
    finally:
        if process:
            kernel.CloseHandle(process)
    return {
        "handle": int(handle),
        "pid": pid.value,
        "process": executable.value.rsplit("\\", 1)[-1].lower(),
        "title": title.value,
        "bounds": [rect.left, rect.top, rect.right, rect.bottom],
    }


def input_arguments(action, options, snapshot):
    """Reject stale observations and points outside the same foreground window."""
    if (
        options.get("snapshot_id") != snapshot.get("id")
        or time.monotonic() - snapshot.get("at", 0) > 90
    ):
        raise DesktopError(
            "Snapshot is missing or stale. View the window again before input."
        )
    current = foreground()
    if current.get("process") in {
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "windowsterminal.exe",
        "regedit.exe",
        "consent.exe",
    }:
        raise DesktopError(
            "Command shells and Windows security dialogs are outside desktop chat controls."
        )
    if any(current[k] != snapshot["window"][k] for k in ("handle", "pid", "bounds")):
        raise DesktopError(
            "The foreground window changed. No input was sent; view again."
        )
    if action == "key":
        key = options.get("key", "").lower()
        if not re.fullmatch(
            r"[a-z0-9]|enter|esc|escape|tab|space|backspace|delete|left|right|up|down|home|end|pageup|pagedown|f[1-9]|ctrl\+(?:a|c|f|s|z|y)|shift\+tab",
            key,
        ):
            raise DesktopError("Use a normal app key or supported editing shortcut.")
        return "Shortcut", {"shortcut": key}
    loc = options.get("loc")
    if (
        not isinstance(loc, list)
        or len(loc) != 2
        or any(type(x) is not int for x in loc)
    ):
        raise DesktopError("Use loc=[screen_x,screen_y] from the latest screenshot.")
    left, top, right, bottom = current["bounds"]
    if not (left <= loc[0] < right and top <= loc[1] < bottom):
        raise DesktopError("That point is outside the observed window.")
    user = ctypes.windll.user32
    user.WindowFromPhysicalPoint.restype = wintypes.HWND
    user.WindowFromPhysicalPoint.argtypes = [wintypes.POINT]
    user.GetAncestor.restype = wintypes.HWND
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    at_point = user.WindowFromPhysicalPoint(wintypes.POINT(*loc))
    if int(user.GetAncestor(at_point, 2) or 0) != current["handle"]:
        raise DesktopError(
            "Another window covers that point. View again before clicking."
        )
    if action == "click":
        button, clicks = options.get("button", "left"), options.get("clicks", 1)
        if (
            button not in ("left", "right", "middle")
            or type(clicks) is not int
            or clicks not in (1, 2)
        ):
            raise DesktopError("Use a single or double mouse click.")
        return "Click", {"loc": loc, "button": button, "clicks": clicks}
    if action == "type":
        text, clear = options.get("text", ""), options.get("clear", False)
        if not isinstance(text, str) or len(text) > 2000 or type(clear) is not bool:
            raise DesktopError(
                "Type at most 2000 characters; clear must be true or false."
            )
        return "Type", {"loc": loc, "text": text, "clear": clear, "press_enter": False}
    if action == "scroll":
        direction = options.get("direction", "down")
        if direction not in ("up", "down"):
            raise DesktopError("Scroll up or down.")
        return "Scroll", {"loc": loc, "direction": direction, "wheel_times": 2}
    raise DesktopError("Unsupported desktop action.")
