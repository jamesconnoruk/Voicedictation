"""
output.py — deliver transcribed text to the currently-focused app.

The whole point of VoxKey: you speak, and the words land at your cursor as one
pasted chunk. That means:

  1. put the text on the clipboard (native Win32 first, pyperclip as fallback),
  2. make sure NO modifier keys are still logically down — after holding
     Ctrl+Shift as your hotkey, Windows can still think Ctrl/Shift are pressed
     when we fire Ctrl+V, which turns the paste into Ctrl+Shift+V (or nothing
     at all in many apps). This was the classic "it records but nothing gets
     typed" failure,
  3. send Ctrl+V with the native SendInput API (more reliable than pynput while
     a low-level keyboard hook is installed),
  4. restore the previous clipboard a beat later.

`type` mode simulates raw keystrokes instead, for apps that block paste.
"""
from __future__ import annotations
import os
import time

_IS_WIN = os.name == "nt"

# Virtual key codes
VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN = 0x11, 0x10, 0x12, 0x5B, 0x5C
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


def active_window_title() -> str:  # pragma: no cover
    """Best-effort title of the foreground window."""
    try:
        if _IS_WIN:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
    except Exception:
        pass
    return ""


def foreground_hwnd():  # pragma: no cover
    """Handle of the window with focus right now (captured when you start
    speaking, so we can paste back into it afterwards)."""
    if not _IS_WIN:
        return None
    try:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return None


def _restore_focus(hwnd) -> bool:  # pragma: no cover
    """
    Put focus back on the window that was active when dictation started.

    Windows refuses SetForegroundWindow from a process that doesn't own the
    foreground, so we temporarily attach to that window's input thread — the
    standard way round it — and then detach again.
    """
    if not _IS_WIN or not hwnd:
        return False
    try:
        import ctypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        if not u32.IsWindow(hwnd):
            return False
        if u32.GetForegroundWindow() == hwnd:
            return True

        cur = u32.GetForegroundWindow()
        tid_target = u32.GetWindowThreadProcessId(hwnd, None)
        tid_cur = u32.GetWindowThreadProcessId(cur, None) if cur else 0
        tid_self = k32.GetCurrentThreadId()

        attached = []
        for tid in {tid_target, tid_cur}:
            if tid and tid != tid_self and u32.AttachThreadInput(tid_self, tid, True):
                attached.append(tid)
        try:
            if u32.IsIconic(hwnd):
                u32.ShowWindow(hwnd, 9)  # SW_RESTORE
            u32.SetForegroundWindow(hwnd)
            u32.SetFocus(hwnd)
        finally:
            for tid in attached:
                u32.AttachThreadInput(tid_self, tid, False)
        time.sleep(0.04)
        return u32.GetForegroundWindow() == hwnd
    except Exception:
        return False


# Accepted paste_method values.
#   "paste"  — copy to clipboard, then press Ctrl+V for you (the Wispr feel)
#   "type"   — simulate the keystrokes directly (works where paste is blocked)
#   "copy"   — copy only; you press Ctrl+V yourself
# "clipboard" is kept as an alias for "paste" so older configs still work.
PASTE_METHODS = ("paste", "type", "copy")


def paste_text(text: str, method: str = "paste",
               target_hwnd=None) -> None:  # pragma: no cover
    if not text:
        return
    if method == "clipboard":
        method = "paste"

    if method == "copy":
        _set_clipboard(text)
        return

    if method == "type":
        _restore_focus(target_hwnd)
        _type_text(text)
        return

    _clipboard_paste(text, target_hwnd)


# --------------------------------------------------------------- clipboard
def _win_set_clipboard(text: str) -> bool:  # pragma: no cover
    """Set the clipboard using Win32 directly (no third-party dependency)."""
    try:
        import ctypes
        from ctypes import wintypes
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32

        # Declare argtypes/restypes explicitly. Without this, ctypes truncates
        # 64-bit HANDLEs to a C int and SetClipboardData raises
        # "OverflowError: int too long to convert" — the native clipboard path
        # then silently never worked on 64-bit Windows.
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalUnlock.restype = wintypes.BOOL
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u32.SetClipboardData.restype = wintypes.HANDLE
        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.OpenClipboard.restype = wintypes.BOOL

        # opening the clipboard can fail if another app holds it — retry
        for _ in range(10):
            if u32.OpenClipboard(None):
                break
            time.sleep(0.02)
        else:
            return False
        try:
            u32.EmptyClipboard()
            data = ctypes.create_unicode_buffer(text)
            size = ctypes.sizeof(data)
            h = k32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h:
                return False
            ptr = k32.GlobalLock(h)
            ctypes.memmove(ptr, ctypes.byref(data), size)
            k32.GlobalUnlock(h)
            u32.SetClipboardData(CF_UNICODETEXT, h)
            return True
        finally:
            u32.CloseClipboard()
    except Exception:
        return False


def _set_clipboard(text: str) -> bool:  # pragma: no cover
    if _IS_WIN and _win_set_clipboard(text):
        return True
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _get_clipboard() -> str:  # pragma: no cover
    try:
        import pyperclip
        return pyperclip.paste()
    except Exception:
        return ""


# --------------------------------------------------------------- keys
def _release_stuck_modifiers():  # pragma: no cover
    """
    Force every modifier UP before we paste. After a hold-to-talk hotkey the
    OS often still believes Ctrl/Shift are down; then our Ctrl+V arrives as
    Ctrl+Shift+V and nothing is inserted.
    """
    if not _IS_WIN:
        return
    try:
        import ctypes
        u32 = ctypes.windll.user32
        for vk in (VK_LCONTROL, VK_RCONTROL, VK_CONTROL,
                   VK_LSHIFT, VK_RSHIFT, VK_SHIFT,
                   VK_LMENU, VK_RMENU, VK_MENU, VK_LWIN, VK_RWIN):
            if u32.GetAsyncKeyState(vk) & 0x8000:
                u32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
    except Exception:
        pass


def _win_ctrl_v() -> bool:  # pragma: no cover
    try:
        import ctypes
        u32 = ctypes.windll.user32
        u32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.01)
        u32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.01)
        u32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)
        u32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False


def _pynput_ctrl_v():  # pragma: no cover
    try:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        with kb.pressed(Key.ctrl):
            kb.press("v")
            kb.release("v")
    except Exception:
        pass


def _clipboard_paste(text: str, target_hwnd=None) -> None:  # pragma: no cover
    prev = _get_clipboard()

    if not _set_clipboard(text):
        # last resort: type it out rather than silently doing nothing
        _restore_focus(target_hwnd)
        _type_text(text)
        return

    # Put focus back where it was BEFORE we paste. Without this the keystroke
    # can land on the overlay, the tray, or whatever else grabbed focus while
    # you were speaking — the clipboard is right but nothing appears.
    restored = _restore_focus(target_hwnd)

    # Let the hotkey release settle, then make sure no modifier is still held.
    time.sleep(0.06)
    _release_stuck_modifiers()

    if not (_IS_WIN and _win_ctrl_v()):
        _pynput_ctrl_v()

    if target_hwnd and not restored:
        from .applog import log
        log("paste: could not restore focus to the original window; "
            "the text is on the clipboard (Ctrl+V to place it)")

    # Restore the previous clipboard once the target app has taken the paste.
    time.sleep(0.25)
    if prev:
        try:
            _set_clipboard(prev)
        except Exception:
            pass


def _type_text(text: str) -> None:  # pragma: no cover
    _release_stuck_modifiers()
    try:
        from pynput.keyboard import Controller
        kb = Controller()
        kb.type(text)
    except Exception:
        pass
