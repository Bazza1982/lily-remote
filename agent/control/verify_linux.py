"""Linux verification implementation using Xlib/pynput."""

import logging
from typing import Optional

from .verify_base import VerifierBase, WindowInfo

logger = logging.getLogger(__name__)

# Try to import pynput for cursor position
PYNPUT_AVAILABLE = False
try:
    from pynput import mouse
    PYNPUT_AVAILABLE = True
except ImportError:
    pass

# Try to import Xlib for window info
XLIB_AVAILABLE = False
try:
    from Xlib import display, X
    from Xlib.protocol import rq
    XLIB_AVAILABLE = True
except ImportError:
    pass


class LinuxVerifier(VerifierBase):
    """Linux implementation of verifier using Xlib/pynput."""

    def __init__(self):
        self._display = None
        self._mouse_controller = None

        if XLIB_AVAILABLE:
            try:
                self._display = display.Display()
            except Exception as e:
                logger.warning("Failed to open X display: %s", e)

        if PYNPUT_AVAILABLE:
            try:
                self._mouse_controller = mouse.Controller()
            except Exception as e:
                logger.warning("Failed to create mouse controller: %s", e)

    def get_cursor_position(self) -> tuple[int, int]:
        """Get current cursor position."""
        # Try pynput first
        if self._mouse_controller:
            try:
                pos = self._mouse_controller.position
                return int(pos[0]), int(pos[1])
            except Exception:
                pass

        # Fall back to Xlib
        if self._display:
            try:
                root = self._display.screen().root
                pointer = root.query_pointer()
                return pointer.root_x, pointer.root_y
            except Exception:
                pass

        logger.warning("Could not get cursor position")
        return 0, 0

    def get_foreground_window_info(self) -> Optional[WindowInfo]:
        """Get information about the foreground (focused) window."""
        if not self._display:
            return None

        try:
            # Get active window from _NET_ACTIVE_WINDOW
            root = self._display.screen().root
            atom = self._display.intern_atom('_NET_ACTIVE_WINDOW')
            response = root.get_full_property(atom, X.AnyPropertyType)

            if response and response.value:
                window_id = response.value[0]
                if window_id:
                    window = self._display.create_resource_object('window', window_id)
                    return self._get_window_info(window, window_id)
        except Exception as e:
            logger.warning("Failed to get foreground window: %s", e)

        return None

    def _get_window_info(self, window, window_id: int) -> Optional[WindowInfo]:
        """Get detailed information about a window."""
        try:
            # Get window title
            title = self._get_window_name(window)

            # Get window class
            class_name = ""
            try:
                wm_class = window.get_wm_class()
                if wm_class:
                    class_name = wm_class[1] if len(wm_class) > 1 else wm_class[0]
            except Exception:
                pass

            # Get window geometry
            rect = None
            try:
                geom = window.get_geometry()
                # Translate to root coordinates
                coords = window.translate_coords(self._display.screen().root, 0, 0)
                rect = (
                    -coords.x,
                    -coords.y,
                    -coords.x + geom.width,
                    -coords.y + geom.height,
                )
            except Exception:
                pass

            # Get PID
            pid = self._get_window_pid(window)

            return WindowInfo(
                hwnd=window_id,
                title=title,
                class_name=class_name,
                process_id=pid,
                rect=rect,
            )
        except Exception as e:
            logger.warning("Failed to get window info: %s", e)
            return None

    def _get_window_name(self, window) -> str:
        """Get window name/title."""
        # Try _NET_WM_NAME first (UTF-8)
        try:
            atom = self._display.intern_atom('_NET_WM_NAME')
            name = window.get_full_property(atom, 0)
            if name:
                return name.value.decode('utf-8', errors='replace')
        except Exception:
            pass

        # Fall back to WM_NAME
        try:
            name = window.get_wm_name()
            if name:
                return name if isinstance(name, str) else name.decode('utf-8', errors='replace')
        except Exception:
            pass

        return ""

    def _get_window_pid(self, window) -> int:
        """Get window's process ID."""
        try:
            atom = self._display.intern_atom('_NET_WM_PID')
            pid = window.get_full_property(atom, X.AnyPropertyType)
            if pid:
                return pid.value[0]
        except Exception:
            pass
        return 0
