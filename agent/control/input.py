"""Cross-platform input controller for mouse and keyboard control.

This module provides a unified API for input injection that works on
both Windows and Linux. The appropriate platform-specific implementation
is automatically selected at runtime.

Supports: click, move, drag, type, hotkey, scroll with DPI awareness.

Usage:
    from agent.control.input import get_controller, click, move, type_text

    # Using module-level convenience functions
    click(100, 200)
    type_text("Hello, World!")

    # Or using the controller directly
    controller = get_controller()
    controller.click(100, 200)
"""

import logging
import sys
from typing import Optional

from .input_base import InputControllerBase, InputResult

logger = logging.getLogger(__name__)

# Determine the current platform
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

# The actual InputController class will be set based on platform
InputController: Optional[type[InputControllerBase]] = None
INPUT_AVAILABLE = False
INPUT_ERROR: Optional[str] = None

if IS_WINDOWS:
    try:
        from .input_windows import WindowsInputController as InputController
        INPUT_AVAILABLE = True
        logger.debug("Using Windows input controller")
    except ImportError as e:
        INPUT_ERROR = str(e)
        logger.error("Windows input controller not available: %s", e)
elif IS_LINUX or IS_MACOS:
    try:
        from .input_linux import LinuxInputController as InputController
        INPUT_AVAILABLE = True
        logger.debug("Using Linux/pynput input controller")
    except ImportError as e:
        INPUT_ERROR = f"pynput is not installed. Install it with: pip install pynput"
        logger.warning("Linux input controller not available: %s", e)
    except Exception as e:
        # pynput may raise other errors on headless systems
        INPUT_ERROR = str(e)
        logger.warning("Linux input controller failed to initialize: %s", e)
else:
    INPUT_ERROR = f"Unsupported platform: {sys.platform}"
    logger.error(INPUT_ERROR)


# Re-export InputResult for convenience
__all__ = [
    "InputController",
    "InputResult",
    "INPUT_AVAILABLE",
    "get_controller",
    "is_input_available",
    "get_input_error",
    "move",
    "click",
    "double_click",
    "right_click",
    "drag",
    "scroll",
    "key_down",
    "key_up",
    "key_press",
    "hotkey",
    "type_text",
    "get_cursor_position",
    "get_screen_size",
]


# =============================================================================
# Module-level convenience functions
# =============================================================================

_controller: Optional[InputControllerBase] = None


def get_controller() -> InputControllerBase:
    """Get or create the global InputController instance.
    
    Raises:
        ImportError: If no input controller is available on this platform.
    """
    global _controller
    if not INPUT_AVAILABLE or InputController is None:
        raise ImportError(INPUT_ERROR or "No input controller available")
    if _controller is None:
        _controller = InputController()
    return _controller


def is_input_available() -> bool:
    """Check if input control is available on this platform."""
    return INPUT_AVAILABLE


def get_input_error() -> Optional[str]:
    """Get the error message if input control is not available."""
    return INPUT_ERROR


def move(x: int, y: int) -> InputResult:
    """Move the mouse cursor to the specified coordinates."""
    return get_controller().move(x, y)


def click(x: int, y: int, button: str = "left", count: int = 1) -> InputResult:
    """Click the mouse at the specified coordinates."""
    return get_controller().click(x, y, button=button, count=count)


def double_click(x: int, y: int, button: str = "left") -> InputResult:
    """Double-click the mouse at the specified coordinates."""
    return get_controller().double_click(x, y, button=button)


def right_click(x: int, y: int) -> InputResult:
    """Right-click the mouse at the specified coordinates."""
    return get_controller().right_click(x, y)


def drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    button: str = "left",
    duration: float = 0.5,
    steps: int = 20,
) -> InputResult:
    """Drag the mouse from start to end coordinates."""
    return get_controller().drag(
        start_x, start_y, end_x, end_y,
        button=button, duration=duration, steps=steps
    )


def scroll(
    delta: int,
    x: Optional[int] = None,
    y: Optional[int] = None,
    horizontal: bool = False,
) -> InputResult:
    """Scroll the mouse wheel."""
    return get_controller().scroll(delta, x=x, y=y, horizontal=horizontal)


def key_down(key: str) -> InputResult:
    """Press a key down (without releasing)."""
    return get_controller().key_down(key)


def key_up(key: str) -> InputResult:
    """Release a key."""
    return get_controller().key_up(key)


def key_press(key: str) -> InputResult:
    """Press and release a key."""
    return get_controller().key_press(key)


def hotkey(*keys: str) -> InputResult:
    """Press a key combination (hotkey/chord)."""
    return get_controller().hotkey(*keys)


def type_text(text: str, interval: float = 0.0, use_unicode: bool = True) -> InputResult:
    """Type text string."""
    return get_controller().type_text(text, interval=interval, use_unicode=use_unicode)


def get_cursor_position() -> tuple[int, int]:
    """Get the current cursor position."""
    return get_controller().get_cursor_position()


def get_screen_size() -> tuple[int, int]:
    """Get the primary screen size."""
    return get_controller().get_screen_size()
