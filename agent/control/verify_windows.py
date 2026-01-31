"""Windows verification implementation using Win32 API."""

import ctypes
import ctypes.wintypes
import logging
from typing import Optional

from .verify_base import VerifierBase, WindowInfo, VerificationState

logger = logging.getLogger(__name__)

# Win32 Constants
GA_ROOT = 2

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.wintypes.LONG), ("y", ctypes.wintypes.LONG)]

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.wintypes.LONG),
        ("top", ctypes.wintypes.LONG),
        ("right", ctypes.wintypes.LONG),
        ("bottom", ctypes.wintypes.LONG),
    ]

class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("hwndActive", ctypes.wintypes.HWND),
        ("hwndFocus", ctypes.wintypes.HWND),
        ("hwndCapture", ctypes.wintypes.HWND),
        ("hwndMenuOwner", ctypes.wintypes.HWND),
        ("hwndMoveSize", ctypes.wintypes.HWND),
        ("hwndCaret", ctypes.wintypes.HWND),
        ("rcCaret", RECT),
    ]

user32 = ctypes.windll.user32


class WindowsVerifier(VerifierBase):
    """Windows implementation of verifier using Win32 API."""

    def get_cursor_position(self) -> tuple[int, int]:
        """Get current cursor position."""
        point = POINT()
        if user32.GetCursorPos(ctypes.byref(point)):
            return point.x, point.y
        return 0, 0

    def get_foreground_window_info(self) -> Optional[WindowInfo]:
        """Get information about the foreground window."""
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        return self._get_window_info(hwnd)

    def _get_window_info(self, hwnd: int) -> Optional[WindowInfo]:
        """Get detailed information about a window."""
        if not hwnd:
            return None
        try:
            return WindowInfo(
                hwnd=hwnd,
                title=self._get_window_text(hwnd),
                class_name=self._get_window_class(hwnd),
                process_id=self._get_window_process_id(hwnd),
                rect=self._get_window_rect(hwnd),
            )
        except Exception as e:
            logger.warning("Failed to get window info: %s", e)
            return None

    def _get_window_text(self, hwnd: int) -> str:
        """Get window title."""
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _get_window_class(self, hwnd: int) -> str:
        """Get window class name."""
        if not hwnd:
            return ""
        buffer = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, buffer, 256):
            return buffer.value
        return ""

    def _get_window_rect(self, hwnd: int) -> Optional[tuple[int, int, int, int]]:
        """Get window bounding rectangle."""
        if not hwnd:
            return None
        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.left, rect.top, rect.right, rect.bottom
        return None

    def _get_window_process_id(self, hwnd: int) -> int:
        """Get window's process ID."""
        if not hwnd:
            return 0
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def window_from_point(self, x: int, y: int) -> int:
        """Get window handle at screen coordinate."""
        point = POINT(x, y)
        return user32.WindowFromPoint(point)

    def get_root_window(self, hwnd: int) -> int:
        """Get root owner window."""
        if not hwnd:
            return 0
        return user32.GetAncestor(hwnd, GA_ROOT)

    def capture_state(self, include_window_at_cursor: bool = False) -> VerificationState:
        """Capture current system state with Windows-specific features."""
        import time
        timestamp = time.time()
        cursor_x, cursor_y = self.get_cursor_position()
        foreground_window = self.get_foreground_window_info()

        window_at_cursor = None
        if include_window_at_cursor:
            hwnd_at_cursor = self.window_from_point(cursor_x, cursor_y)
            if hwnd_at_cursor:
                root_hwnd = self.get_root_window(hwnd_at_cursor)
                window_at_cursor = self._get_window_info(root_hwnd or hwnd_at_cursor)

        focused_window = None
        gui_info = GUITHREADINFO()
        gui_info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(0, ctypes.byref(gui_info)):
            if gui_info.hwndFocus:
                focused_window = self._get_window_info(gui_info.hwndFocus)

        return VerificationState(
            timestamp=timestamp,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            foreground_window=foreground_window,
            window_at_cursor=window_at_cursor,
            focused_window=focused_window,
        )
