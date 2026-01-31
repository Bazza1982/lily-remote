"""Linux pynput implementation for mouse and keyboard control.

This module uses pynput for cross-platform input injection on Linux.
Requires X11 or Wayland with appropriate permissions.

Supports: click, move, drag, type, hotkey, scroll.
"""

import logging
import time
from typing import Optional

from .input_base import InputControllerBase, InputResult, normalize_key_name

logger = logging.getLogger(__name__)

# Try to import pynput - it's optional and only needed on Linux
try:
    from pynput import mouse as pynput_mouse
    from pynput import keyboard as pynput_keyboard
    from pynput.keyboard import Key, KeyCode
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    logger.warning("pynput not available - Linux input control will not work")

# Try to get screen size using various methods
def _get_screen_size_x11() -> Optional[tuple[int, int]]:
    """Get screen size using python-xlib."""
    try:
        from Xlib import display
        d = display.Display()
        screen = d.screen()
        return screen.width_in_pixels, screen.height_in_pixels
    except Exception:
        return None


def _get_screen_size_gtk() -> Optional[tuple[int, int]]:
    """Get screen size using GTK."""
    try:
        import gi
        gi.require_version('Gdk', '3.0')
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display:
            monitor = display.get_primary_monitor()
            if monitor:
                geometry = monitor.get_geometry()
                return geometry.width, geometry.height
    except Exception:
        return None
    return None


def _get_screen_size_tkinter() -> Optional[tuple[int, int]]:
    """Get screen size using tkinter (fallback)."""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        width = root.winfo_screenwidth()
        height = root.winfo_screenheight()
        root.destroy()
        return width, height
    except Exception:
        return None


def _get_screen_size_mss() -> Optional[tuple[int, int]]:
    """Get screen size using mss (should already be installed)."""
    try:
        import mss
        with mss.mss() as sct:
            # Get the first monitor (primary)
            if len(sct.monitors) > 1:
                mon = sct.monitors[1]  # monitors[0] is "all monitors"
                return mon["width"], mon["height"]
            elif sct.monitors:
                mon = sct.monitors[0]
                return mon["width"], mon["height"]
    except Exception:
        return None
    return None


def get_linux_screen_size() -> tuple[int, int]:
    """
    Get screen size on Linux using available methods.

    Returns:
        Tuple of (width, height) in pixels.
        Falls back to (1920, 1080) if detection fails.
    """
    # Try methods in order of preference
    for method in [_get_screen_size_mss, _get_screen_size_x11,
                   _get_screen_size_gtk, _get_screen_size_tkinter]:
        result = method()
        if result:
            return result

    logger.warning("Could not detect screen size, using default 1920x1080")
    return 1920, 1080


# Map key names to pynput Key objects
def _get_pynput_key(key_name: str) -> 'Key | KeyCode | str':
    """
    Convert a key name to a pynput Key or KeyCode.

    Args:
        key_name: The key name to convert.

    Returns:
        pynput Key, KeyCode, or the original string for single characters.

    Raises:
        ValueError: If the key is not recognized.
    """
    if not PYNPUT_AVAILABLE:
        raise RuntimeError("pynput is not available")

    # Normalize the key name
    normalized = normalize_key_name(key_name)

    # Map of normalized names to pynput Key objects
    key_map = {
        # Modifier keys
        "control": Key.ctrl,
        "alt": Key.alt,
        "shift": Key.shift,
        "super": Key.cmd,  # pynput uses cmd for the super/windows key

        # Special keys
        "return": Key.enter,
        "escape": Key.esc,
        "tab": Key.tab,
        "space": Key.space,
        "back": Key.backspace,
        "backspace": Key.backspace,
        "delete": Key.delete,
        "insert": Key.insert,
        "home": Key.home,
        "end": Key.end,
        "prior": Key.page_up,
        "page_up": Key.page_up,
        "next": Key.page_down,
        "page_down": Key.page_down,
        "caps_lock": Key.caps_lock,
        "num_lock": Key.num_lock,
        "scroll_lock": Key.scroll_lock,
        "print_screen": Key.print_screen,
        "pause": Key.pause,

        # Arrow keys
        "left": Key.left,
        "right": Key.right,
        "up": Key.up,
        "down": Key.down,

        # Function keys
        "f1": Key.f1,
        "f2": Key.f2,
        "f3": Key.f3,
        "f4": Key.f4,
        "f5": Key.f5,
        "f6": Key.f6,
        "f7": Key.f7,
        "f8": Key.f8,
        "f9": Key.f9,
        "f10": Key.f10,
        "f11": Key.f11,
        "f12": Key.f12,

        # Media keys
        "volume_mute": Key.media_volume_mute,
        "volume_down": Key.media_volume_down,
        "volume_up": Key.media_volume_up,
        "media_next": Key.media_next,
        "media_prev": Key.media_previous,
        "media_play_pause": Key.media_play_pause,
    }

    # Check if it's a known special key
    if normalized in key_map:
        return key_map[normalized]

    # Single character
    if len(key_name) == 1:
        return key_name

    # Try to parse as a character code or return as-is
    raise ValueError(f"Unknown key: {key_name}")


# Map button names to pynput Button objects
def _get_pynput_button(button: str) -> 'pynput_mouse.Button':
    """
    Convert a button name to a pynput Button.

    Args:
        button: Button name ("left", "right", "middle", "x1", "x2").

    Returns:
        pynput Button object.

    Raises:
        ValueError: If the button is not recognized.
    """
    if not PYNPUT_AVAILABLE:
        raise RuntimeError("pynput is not available")

    button_lower = button.lower()
    button_map = {
        "left": pynput_mouse.Button.left,
        "right": pynput_mouse.Button.right,
        "middle": pynput_mouse.Button.middle,
    }

    if button_lower in button_map:
        return button_map[button_lower]

    # X1/X2 buttons may not be available on all systems
    if button_lower == "x1":
        try:
            return pynput_mouse.Button.x1
        except AttributeError:
            logger.warning("X1 button not supported, falling back to middle")
            return pynput_mouse.Button.middle

    if button_lower == "x2":
        try:
            return pynput_mouse.Button.x2
        except AttributeError:
            logger.warning("X2 button not supported, falling back to middle")
            return pynput_mouse.Button.middle

    raise ValueError(f"Unknown mouse button: {button}")


class LinuxInputController(InputControllerBase):
    """
    pynput-based input controller for Linux.

    Provides mouse and keyboard input injection using pynput.
    Requires X11 or Wayland with appropriate permissions.
    """

    def __init__(self):
        """Initialize the input controller."""
        if not PYNPUT_AVAILABLE:
            raise RuntimeError(
                "pynput is not installed. Install it with: pip install pynput"
            )

        self._mouse = pynput_mouse.Controller()
        self._keyboard = pynput_keyboard.Controller()
        self._screen_size: Optional[tuple[int, int]] = None

        logger.info("LinuxInputController initialized")

    # -------------------------------------------------------------------------
    # Mouse Operations
    # -------------------------------------------------------------------------

    def move(self, x: int, y: int) -> InputResult:
        """
        Move the mouse cursor to absolute screen coordinates.

        Args:
            x: X coordinate in screen pixels.
            y: Y coordinate in screen pixels.

        Returns:
            InputResult indicating success/failure.
        """
        try:
            self._mouse.position = (x, y)
            return InputResult(success=True, events_sent=1)
        except Exception as e:
            logger.error("Mouse move failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        count: int = 1,
    ) -> InputResult:
        """
        Click the mouse at the specified coordinates.

        Args:
            x: X coordinate in screen pixels.
            y: Y coordinate in screen pixels.
            button: Mouse button ("left", "right", "middle", "x1", "x2").
            count: Number of clicks (1 for single, 2 for double).

        Returns:
            InputResult indicating success/failure.
        """
        try:
            pynput_button = _get_pynput_button(button)

            # Move to position first
            move_result = self.move(x, y)
            if not move_result.success:
                return move_result

            total_sent = move_result.events_sent

            # Perform click(s)
            for i in range(count):
                if i > 0:
                    time.sleep(self.DOUBLE_CLICK_DELAY)

                self._mouse.press(pynput_button)
                time.sleep(self.CLICK_DELAY)
                self._mouse.release(pynput_button)
                total_sent += 2  # press + release

            return InputResult(success=True, events_sent=total_sent)

        except ValueError as e:
            return InputResult(success=False, events_sent=0, error=str(e))
        except Exception as e:
            logger.error("Mouse click failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    def drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        button: str = "left",
        duration: float = 0.5,
        steps: int = 20,
    ) -> InputResult:
        """
        Drag the mouse from start to end coordinates.

        Args:
            start_x: Starting X coordinate.
            start_y: Starting Y coordinate.
            end_x: Ending X coordinate.
            end_y: Ending Y coordinate.
            button: Mouse button to hold during drag.
            duration: Total duration of the drag in seconds.
            steps: Number of intermediate points.

        Returns:
            InputResult indicating success/failure.
        """
        try:
            pynput_button = _get_pynput_button(button)
            total_sent = 0
            step_delay = duration / steps if steps > 0 else 0

            # Move to start position
            move_result = self.move(start_x, start_y)
            if not move_result.success:
                return move_result
            total_sent += move_result.events_sent

            # Mouse down
            self._mouse.press(pynput_button)
            total_sent += 1
            time.sleep(self.CLICK_DELAY)

            # Interpolate movement
            for i in range(1, steps + 1):
                t = i / steps
                cur_x = int(start_x + (end_x - start_x) * t)
                cur_y = int(start_y + (end_y - start_y) * t)

                self._mouse.position = (cur_x, cur_y)
                total_sent += 1

                if step_delay > 0:
                    time.sleep(step_delay)

            # Mouse up
            self._mouse.release(pynput_button)
            total_sent += 1

            return InputResult(success=True, events_sent=total_sent)

        except ValueError as e:
            return InputResult(success=False, events_sent=0, error=str(e))
        except Exception as e:
            logger.error("Mouse drag failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    def scroll(
        self,
        delta: int,
        x: Optional[int] = None,
        y: Optional[int] = None,
        horizontal: bool = False,
    ) -> InputResult:
        """
        Scroll the mouse wheel.

        Args:
            delta: Scroll amount. Positive = up/right, negative = down/left.
            x: Optional X coordinate to scroll at (current position if None).
            y: Optional Y coordinate to scroll at (current position if None).
            horizontal: If True, scroll horizontally.

        Returns:
            InputResult indicating success/failure.
        """
        try:
            total_sent = 0

            # Move to position if specified
            if x is not None and y is not None:
                move_result = self.move(x, y)
                if not move_result.success:
                    return move_result
                total_sent += move_result.events_sent

            # Perform scroll
            if horizontal:
                self._mouse.scroll(delta, 0)
            else:
                self._mouse.scroll(0, delta)

            total_sent += 1
            return InputResult(success=True, events_sent=total_sent)

        except Exception as e:
            logger.error("Mouse scroll failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    # -------------------------------------------------------------------------
    # Keyboard Operations
    # -------------------------------------------------------------------------

    def key_down(self, key: str) -> InputResult:
        """
        Press a key down (without releasing).

        Args:
            key: Key name or character.

        Returns:
            InputResult indicating success/failure.
        """
        try:
            pynput_key = _get_pynput_key(key)
            self._keyboard.press(pynput_key)
            return InputResult(success=True, events_sent=1)
        except ValueError as e:
            return InputResult(success=False, events_sent=0, error=str(e))
        except Exception as e:
            logger.error("Key down failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    def key_up(self, key: str) -> InputResult:
        """
        Release a key.

        Args:
            key: Key name or character.

        Returns:
            InputResult indicating success/failure.
        """
        try:
            pynput_key = _get_pynput_key(key)
            self._keyboard.release(pynput_key)
            return InputResult(success=True, events_sent=1)
        except ValueError as e:
            return InputResult(success=False, events_sent=0, error=str(e))
        except Exception as e:
            logger.error("Key up failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    def key_press(self, key: str) -> InputResult:
        """
        Press and release a key.

        Args:
            key: Key name or character.

        Returns:
            InputResult indicating success/failure.
        """
        try:
            pynput_key = _get_pynput_key(key)

            self._keyboard.press(pynput_key)
            time.sleep(self.KEY_PRESS_DELAY)
            self._keyboard.release(pynput_key)

            return InputResult(success=True, events_sent=2)

        except ValueError as e:
            return InputResult(success=False, events_sent=0, error=str(e))
        except Exception as e:
            logger.error("Key press failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    def hotkey(self, *keys: str) -> InputResult:
        """
        Press a key combination (hotkey/chord).

        Args:
            *keys: Keys to press together (e.g., "ctrl", "shift", "s").

        Returns:
            InputResult indicating success/failure.
        """
        if not keys:
            return InputResult(success=False, events_sent=0, error="No keys specified")

        try:
            total_sent = 0
            pressed_keys = []

            # Press all keys down
            for key in keys:
                pynput_key = _get_pynput_key(key)
                self._keyboard.press(pynput_key)
                pressed_keys.append(pynput_key)
                total_sent += 1
                time.sleep(self.KEY_CHORD_DELAY)

            time.sleep(self.KEY_PRESS_DELAY)

            # Release all keys in reverse order
            for pynput_key in reversed(pressed_keys):
                self._keyboard.release(pynput_key)
                total_sent += 1
                time.sleep(self.KEY_CHORD_DELAY)

            return InputResult(success=True, events_sent=total_sent)

        except ValueError as e:
            return InputResult(success=False, events_sent=0, error=str(e))
        except Exception as e:
            logger.error("Hotkey failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    def type_text(
        self,
        text: str,
        interval: float = 0.0,
        use_unicode: bool = True,
    ) -> InputResult:
        """
        Type text string.

        Args:
            text: Text to type.
            interval: Delay between characters in seconds.
            use_unicode: If True, use Unicode input (pynput handles this automatically).

        Returns:
            InputResult indicating success/failure.
        """
        if not text:
            return InputResult(success=True, events_sent=0)

        try:
            total_sent = 0
            actual_interval = interval if interval > 0 else self.TYPE_CHAR_DELAY

            for i, char in enumerate(text):
                # pynput's type method handles unicode
                self._keyboard.type(char)
                total_sent += 1

                # Delay between characters (skip on last char)
                if i < len(text) - 1 and actual_interval > 0:
                    time.sleep(actual_interval)

            return InputResult(success=True, events_sent=total_sent)

        except Exception as e:
            logger.error("Type text failed: %s", e)
            return InputResult(success=False, events_sent=0, error=str(e))

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def get_cursor_position(self) -> tuple[int, int]:
        """
        Get the current cursor position.

        Returns:
            Tuple of (x, y) in screen pixels.
        """
        return self._mouse.position

    def get_screen_size(self) -> tuple[int, int]:
        """
        Get the primary screen size.

        Returns:
            Tuple of (width, height) in pixels.
        """
        if self._screen_size is None:
            self._screen_size = get_linux_screen_size()
        return self._screen_size


# Alias for consistency
InputController = LinuxInputController
