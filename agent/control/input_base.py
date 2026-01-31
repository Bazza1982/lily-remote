"""Abstract base class for platform-specific input controllers.

This module defines the InputController interface that platform-specific
implementations must follow. This enables cross-platform input injection
for mouse and keyboard control.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class InputResult:
    """Result of an input operation."""
    success: bool
    events_sent: int
    error: Optional[str] = None


class InputControllerBase(ABC):
    """
    Abstract base class for input controllers.

    Platform-specific implementations (Windows, Linux) must implement
    all abstract methods to provide mouse and keyboard control.
    """

    # Delays between input events (seconds)
    KEY_PRESS_DELAY = 0.01      # Delay between key down and up
    KEY_CHORD_DELAY = 0.02      # Delay between keys in a chord
    TYPE_CHAR_DELAY = 0.01      # Default delay between typed characters
    CLICK_DELAY = 0.02          # Delay between mouse down and up
    DOUBLE_CLICK_DELAY = 0.05   # Delay between clicks in double-click

    # -------------------------------------------------------------------------
    # Mouse Operations
    # -------------------------------------------------------------------------

    @abstractmethod
    def move(self, x: int, y: int) -> InputResult:
        """
        Move the mouse cursor to absolute screen coordinates.

        Args:
            x: X coordinate in screen pixels.
            y: Y coordinate in screen pixels.

        Returns:
            InputResult indicating success/failure.
        """
        pass

    @abstractmethod
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
        pass

    def double_click(self, x: int, y: int, button: str = "left") -> InputResult:
        """
        Double-click the mouse at the specified coordinates.

        Args:
            x: X coordinate in screen pixels.
            y: Y coordinate in screen pixels.
            button: Mouse button ("left", "right", "middle").

        Returns:
            InputResult indicating success/failure.
        """
        return self.click(x, y, button=button, count=2)

    def right_click(self, x: int, y: int) -> InputResult:
        """
        Right-click the mouse at the specified coordinates.

        Args:
            x: X coordinate in screen pixels.
            y: Y coordinate in screen pixels.

        Returns:
            InputResult indicating success/failure.
        """
        return self.click(x, y, button="right")

    @abstractmethod
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
        pass

    @abstractmethod
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
        pass

    # -------------------------------------------------------------------------
    # Keyboard Operations
    # -------------------------------------------------------------------------

    @abstractmethod
    def key_down(self, key: str) -> InputResult:
        """
        Press a key down (without releasing).

        Args:
            key: Key name or character.

        Returns:
            InputResult indicating success/failure.
        """
        pass

    @abstractmethod
    def key_up(self, key: str) -> InputResult:
        """
        Release a key.

        Args:
            key: Key name or character.

        Returns:
            InputResult indicating success/failure.
        """
        pass

    @abstractmethod
    def key_press(self, key: str) -> InputResult:
        """
        Press and release a key.

        Args:
            key: Key name or character.

        Returns:
            InputResult indicating success/failure.
        """
        pass

    @abstractmethod
    def hotkey(self, *keys: str) -> InputResult:
        """
        Press a key combination (hotkey/chord).

        Args:
            *keys: Keys to press together (e.g., "ctrl", "shift", "s").

        Returns:
            InputResult indicating success/failure.
        """
        pass

    @abstractmethod
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
            use_unicode: If True, use Unicode input (recommended for special chars).

        Returns:
            InputResult indicating success/failure.
        """
        pass

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    @abstractmethod
    def get_cursor_position(self) -> tuple[int, int]:
        """
        Get the current cursor position.

        Returns:
            Tuple of (x, y) in screen pixels.
        """
        pass

    @abstractmethod
    def get_screen_size(self) -> tuple[int, int]:
        """
        Get the primary screen size.

        Returns:
            Tuple of (width, height) in pixels.
        """
        pass

    def get_virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        """
        Get the virtual screen bounds (all monitors).

        Returns:
            Tuple of (left, top, width, height).

        Note:
            Default implementation returns primary screen starting at (0, 0).
            Platform-specific implementations may override for multi-monitor support.
        """
        width, height = self.get_screen_size()
        return 0, 0, width, height


# Key name normalization for cross-platform compatibility
# Maps common key names to a canonical form
KEY_NAME_ALIASES: dict[str, str] = {
    # Modifier keys
    "ctrl": "control",
    "lctrl": "control",
    "rctrl": "control",
    "cmd": "super",  # macOS command key
    "win": "super",
    "lwin": "super",
    "rwin": "super",
    "lalt": "alt",
    "ralt": "alt",
    "lshift": "shift",
    "rshift": "shift",

    # Special keys
    "enter": "return",
    "esc": "escape",
    "backspace": "back",
    "del": "delete",
    "ins": "insert",
    "pageup": "prior",
    "pgup": "prior",
    "pagedown": "next",
    "pgdn": "next",
    "capslock": "caps_lock",
    "caps": "caps_lock",
    "numlock": "num_lock",
    "scrolllock": "scroll_lock",
    "printscreen": "print_screen",
    "prtsc": "print_screen",

    # Numpad aliases
    "num0": "numpad0",
    "num1": "numpad1",
    "num2": "numpad2",
    "num3": "numpad3",
    "num4": "numpad4",
    "num5": "numpad5",
    "num6": "numpad6",
    "num7": "numpad7",
    "num8": "numpad8",
    "num9": "numpad9",
    "nummul": "numpad_multiply",
    "numadd": "numpad_add",
    "numsub": "numpad_subtract",
    "numdec": "numpad_decimal",
    "numdiv": "numpad_divide",

    # Media keys
    "volumemute": "volume_mute",
    "volumedown": "volume_down",
    "volumeup": "volume_up",
    "medianext": "media_next",
    "mediaprev": "media_prev",
    "mediastop": "media_stop",
    "mediaplaypause": "media_play_pause",

    # Symbol aliases
    "semicolon": ";",
    "equals": "=",
    "plus": "=",
    "comma": ",",
    "minus": "-",
    "period": ".",
    "slash": "/",
    "backtick": "`",
    "bracketleft": "[",
    "backslash": "\\",
    "bracketright": "]",
    "quote": "'",
}


def normalize_key_name(key: str) -> str:
    """
    Normalize a key name to a canonical form for cross-platform use.

    Args:
        key: The key name to normalize.

    Returns:
        Normalized key name.
    """
    key_lower = key.lower()
    return KEY_NAME_ALIASES.get(key_lower, key_lower)
