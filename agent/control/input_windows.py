"""Win32 SendInput implementation for mouse and keyboard control.

This module uses ctypes + Win32 SendInput (NOT PyAutoGUI) for reliable
input injection that works with UAC and elevated windows.

Supports: click, move, drag, type, hotkey, scroll with DPI awareness.
"""

import ctypes
import ctypes.wintypes
import logging
import time
from enum import IntEnum
from typing import Optional

from .input_base import InputControllerBase, InputResult

logger = logging.getLogger(__name__)

# =============================================================================
# Win32 Constants
# =============================================================================

# Input types for SendInput
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

# Mouse event flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

# Keyboard event flags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# Mouse button constants for XBUTTON
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

# Virtual scroll units
WHEEL_DELTA = 120

# DPI awareness constants
PROCESS_DPI_UNAWARE = 0
PROCESS_SYSTEM_DPI_AWARE = 1
PROCESS_PER_MONITOR_DPI_AWARE = 2

# System metrics
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77


# =============================================================================
# Virtual Key Codes
# =============================================================================

class VK(IntEnum):
    """Virtual key codes for keyboard input."""
    # Mouse buttons (for completeness)
    LBUTTON = 0x01
    RBUTTON = 0x02
    CANCEL = 0x03
    MBUTTON = 0x04
    XBUTTON1 = 0x05
    XBUTTON2 = 0x06

    # Control keys
    BACK = 0x08
    TAB = 0x09
    CLEAR = 0x0C
    RETURN = 0x0D
    SHIFT = 0x10
    CONTROL = 0x11
    MENU = 0x12  # Alt key
    PAUSE = 0x13
    CAPITAL = 0x14  # Caps Lock
    ESCAPE = 0x1B
    SPACE = 0x20
    PRIOR = 0x21  # Page Up
    NEXT = 0x22   # Page Down
    END = 0x23
    HOME = 0x24
    LEFT = 0x25
    UP = 0x26
    RIGHT = 0x27
    DOWN = 0x28
    SELECT = 0x29
    PRINT = 0x2A
    EXECUTE = 0x2B
    SNAPSHOT = 0x2C  # Print Screen
    INSERT = 0x2D
    DELETE = 0x2E
    HELP = 0x2F

    # Numbers (0-9)
    KEY_0 = 0x30
    KEY_1 = 0x31
    KEY_2 = 0x32
    KEY_3 = 0x33
    KEY_4 = 0x34
    KEY_5 = 0x35
    KEY_6 = 0x36
    KEY_7 = 0x37
    KEY_8 = 0x38
    KEY_9 = 0x39

    # Letters (A-Z)
    KEY_A = 0x41
    KEY_B = 0x42
    KEY_C = 0x43
    KEY_D = 0x44
    KEY_E = 0x45
    KEY_F = 0x46
    KEY_G = 0x47
    KEY_H = 0x48
    KEY_I = 0x49
    KEY_J = 0x4A
    KEY_K = 0x4B
    KEY_L = 0x4C
    KEY_M = 0x4D
    KEY_N = 0x4E
    KEY_O = 0x4F
    KEY_P = 0x50
    KEY_Q = 0x51
    KEY_R = 0x52
    KEY_S = 0x53
    KEY_T = 0x54
    KEY_U = 0x55
    KEY_V = 0x56
    KEY_W = 0x57
    KEY_X = 0x58
    KEY_Y = 0x59
    KEY_Z = 0x5A

    # Windows keys
    LWIN = 0x5B
    RWIN = 0x5C
    APPS = 0x5D

    # Numpad
    NUMPAD0 = 0x60
    NUMPAD1 = 0x61
    NUMPAD2 = 0x62
    NUMPAD3 = 0x63
    NUMPAD4 = 0x64
    NUMPAD5 = 0x65
    NUMPAD6 = 0x66
    NUMPAD7 = 0x67
    NUMPAD8 = 0x68
    NUMPAD9 = 0x69
    MULTIPLY = 0x6A
    ADD = 0x6B
    SEPARATOR = 0x6C
    SUBTRACT = 0x6D
    DECIMAL = 0x6E
    DIVIDE = 0x6F

    # Function keys
    F1 = 0x70
    F2 = 0x71
    F3 = 0x72
    F4 = 0x73
    F5 = 0x74
    F6 = 0x75
    F7 = 0x76
    F8 = 0x77
    F9 = 0x78
    F10 = 0x79
    F11 = 0x7A
    F12 = 0x7B
    F13 = 0x7C
    F14 = 0x7D
    F15 = 0x7E
    F16 = 0x7F
    F17 = 0x80
    F18 = 0x81
    F19 = 0x82
    F20 = 0x83
    F21 = 0x84
    F22 = 0x85
    F23 = 0x86
    F24 = 0x87

    # Lock keys
    NUMLOCK = 0x90
    SCROLL = 0x91

    # Modifier keys (left/right specific)
    LSHIFT = 0xA0
    RSHIFT = 0xA1
    LCONTROL = 0xA2
    RCONTROL = 0xA3
    LMENU = 0xA4  # Left Alt
    RMENU = 0xA5  # Right Alt

    # Browser keys
    BROWSER_BACK = 0xA6
    BROWSER_FORWARD = 0xA7
    BROWSER_REFRESH = 0xA8
    BROWSER_STOP = 0xA9
    BROWSER_SEARCH = 0xAA
    BROWSER_FAVORITES = 0xAB
    BROWSER_HOME = 0xAC

    # Media keys
    VOLUME_MUTE = 0xAD
    VOLUME_DOWN = 0xAE
    VOLUME_UP = 0xAF
    MEDIA_NEXT_TRACK = 0xB0
    MEDIA_PREV_TRACK = 0xB1
    MEDIA_STOP = 0xB2
    MEDIA_PLAY_PAUSE = 0xB3

    # OEM keys
    OEM_1 = 0xBA      # ;:
    OEM_PLUS = 0xBB   # =+
    OEM_COMMA = 0xBC  # ,<
    OEM_MINUS = 0xBD  # -_
    OEM_PERIOD = 0xBE # .>
    OEM_2 = 0xBF      # /?
    OEM_3 = 0xC0      # `~
    OEM_4 = 0xDB      # [{
    OEM_5 = 0xDC      # \|
    OEM_6 = 0xDD      # ]}
    OEM_7 = 0xDE      # '"


# Key name to VK code mapping
KEY_NAME_MAP: dict[str, int] = {
    # Modifier keys
    "ctrl": VK.CONTROL,
    "control": VK.CONTROL,
    "lctrl": VK.LCONTROL,
    "rctrl": VK.RCONTROL,
    "alt": VK.MENU,
    "lalt": VK.LMENU,
    "ralt": VK.RMENU,
    "shift": VK.SHIFT,
    "lshift": VK.LSHIFT,
    "rshift": VK.RSHIFT,
    "win": VK.LWIN,
    "lwin": VK.LWIN,
    "rwin": VK.RWIN,
    "cmd": VK.LWIN,  # macOS compatibility
    "super": VK.LWIN,

    # Special keys
    "enter": VK.RETURN,
    "return": VK.RETURN,
    "esc": VK.ESCAPE,
    "escape": VK.ESCAPE,
    "tab": VK.TAB,
    "space": VK.SPACE,
    "backspace": VK.BACK,
    "back": VK.BACK,
    "delete": VK.DELETE,
    "del": VK.DELETE,
    "insert": VK.INSERT,
    "ins": VK.INSERT,
    "home": VK.HOME,
    "end": VK.END,
    "pageup": VK.PRIOR,
    "pgup": VK.PRIOR,
    "prior": VK.PRIOR,
    "pagedown": VK.NEXT,
    "pgdn": VK.NEXT,
    "next": VK.NEXT,
    "capslock": VK.CAPITAL,
    "caps": VK.CAPITAL,
    "caps_lock": VK.CAPITAL,
    "numlock": VK.NUMLOCK,
    "num_lock": VK.NUMLOCK,
    "scrolllock": VK.SCROLL,
    "scroll_lock": VK.SCROLL,
    "printscreen": VK.SNAPSHOT,
    "prtsc": VK.SNAPSHOT,
    "print_screen": VK.SNAPSHOT,
    "pause": VK.PAUSE,

    # Arrow keys
    "left": VK.LEFT,
    "right": VK.RIGHT,
    "up": VK.UP,
    "down": VK.DOWN,

    # Function keys
    "f1": VK.F1,
    "f2": VK.F2,
    "f3": VK.F3,
    "f4": VK.F4,
    "f5": VK.F5,
    "f6": VK.F6,
    "f7": VK.F7,
    "f8": VK.F8,
    "f9": VK.F9,
    "f10": VK.F10,
    "f11": VK.F11,
    "f12": VK.F12,

    # Numpad
    "num0": VK.NUMPAD0,
    "num1": VK.NUMPAD1,
    "num2": VK.NUMPAD2,
    "num3": VK.NUMPAD3,
    "num4": VK.NUMPAD4,
    "num5": VK.NUMPAD5,
    "num6": VK.NUMPAD6,
    "num7": VK.NUMPAD7,
    "num8": VK.NUMPAD8,
    "num9": VK.NUMPAD9,
    "numpad0": VK.NUMPAD0,
    "numpad1": VK.NUMPAD1,
    "numpad2": VK.NUMPAD2,
    "numpad3": VK.NUMPAD3,
    "numpad4": VK.NUMPAD4,
    "numpad5": VK.NUMPAD5,
    "numpad6": VK.NUMPAD6,
    "numpad7": VK.NUMPAD7,
    "numpad8": VK.NUMPAD8,
    "numpad9": VK.NUMPAD9,
    "nummul": VK.MULTIPLY,
    "numpad_multiply": VK.MULTIPLY,
    "numadd": VK.ADD,
    "numpad_add": VK.ADD,
    "numsub": VK.SUBTRACT,
    "numpad_subtract": VK.SUBTRACT,
    "numdec": VK.DECIMAL,
    "numpad_decimal": VK.DECIMAL,
    "numdiv": VK.DIVIDE,
    "numpad_divide": VK.DIVIDE,

    # Media keys
    "volumemute": VK.VOLUME_MUTE,
    "volume_mute": VK.VOLUME_MUTE,
    "volumedown": VK.VOLUME_DOWN,
    "volume_down": VK.VOLUME_DOWN,
    "volumeup": VK.VOLUME_UP,
    "volume_up": VK.VOLUME_UP,
    "medianext": VK.MEDIA_NEXT_TRACK,
    "media_next": VK.MEDIA_NEXT_TRACK,
    "mediaprev": VK.MEDIA_PREV_TRACK,
    "media_prev": VK.MEDIA_PREV_TRACK,
    "mediastop": VK.MEDIA_STOP,
    "media_stop": VK.MEDIA_STOP,
    "mediaplaypause": VK.MEDIA_PLAY_PAUSE,
    "media_play_pause": VK.MEDIA_PLAY_PAUSE,

    # Browser keys
    "browserback": VK.BROWSER_BACK,
    "browserforward": VK.BROWSER_FORWARD,
    "browserrefresh": VK.BROWSER_REFRESH,
    "browserstop": VK.BROWSER_STOP,
    "browsersearch": VK.BROWSER_SEARCH,
    "browserfavorites": VK.BROWSER_FAVORITES,
    "browserhome": VK.BROWSER_HOME,

    # OEM keys - common symbols
    ";": VK.OEM_1,
    "semicolon": VK.OEM_1,
    "=": VK.OEM_PLUS,
    "equals": VK.OEM_PLUS,
    "plus": VK.OEM_PLUS,
    ",": VK.OEM_COMMA,
    "comma": VK.OEM_COMMA,
    "-": VK.OEM_MINUS,
    "minus": VK.OEM_MINUS,
    ".": VK.OEM_PERIOD,
    "period": VK.OEM_PERIOD,
    "/": VK.OEM_2,
    "slash": VK.OEM_2,
    "`": VK.OEM_3,
    "backtick": VK.OEM_3,
    "[": VK.OEM_4,
    "bracketleft": VK.OEM_4,
    "\\": VK.OEM_5,
    "backslash": VK.OEM_5,
    "]": VK.OEM_6,
    "bracketright": VK.OEM_6,
    "'": VK.OEM_7,
    "quote": VK.OEM_7,
}

# Extended keys that require the KEYEVENTF_EXTENDEDKEY flag
EXTENDED_KEYS = {
    VK.INSERT, VK.DELETE, VK.HOME, VK.END, VK.PRIOR, VK.NEXT,
    VK.LEFT, VK.RIGHT, VK.UP, VK.DOWN,
    VK.NUMLOCK, VK.DIVIDE, VK.RETURN,  # Numpad Enter
    VK.RCONTROL, VK.RMENU,
    VK.LWIN, VK.RWIN, VK.APPS,
    VK.SNAPSHOT,
}


# =============================================================================
# Win32 Structures
# =============================================================================

class MOUSEINPUT(ctypes.Structure):
    """Structure for mouse input events."""
    _fields_ = [
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG)),
    ]


class KEYBDINPUT(ctypes.Structure):
    """Structure for keyboard input events."""
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.wintypes.ULONG)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    """Structure for hardware input events."""
    _fields_ = [
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    """Union for INPUT structure."""
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    """Main INPUT structure for SendInput."""
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


class POINT(ctypes.Structure):
    """POINT structure for cursor position."""
    _fields_ = [
        ("x", ctypes.wintypes.LONG),
        ("y", ctypes.wintypes.LONG),
    ]


# =============================================================================
# Win32 API Functions
# =============================================================================

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shcore = None

try:
    shcore = ctypes.windll.shcore
except OSError:
    pass  # shcore not available on older Windows versions


def _set_dpi_awareness() -> bool:
    """
    Set process DPI awareness for accurate coordinate handling.

    Returns:
        True if DPI awareness was set successfully.
    """
    try:
        # Try Windows 10+ API first
        if shcore:
            result = shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
            if result == 0:  # S_OK
                logger.debug("Set per-monitor DPI awareness")
                return True

        # Fall back to Windows 8.1+ API
        result = user32.SetProcessDPIAware()
        if result:
            logger.debug("Set system DPI awareness (fallback)")
            return True

    except Exception as e:
        logger.warning("Failed to set DPI awareness: %s", e)

    return False


def _get_screen_size() -> tuple[int, int]:
    """Get the primary screen size in pixels."""
    width = user32.GetSystemMetrics(SM_CXSCREEN)
    height = user32.GetSystemMetrics(SM_CYSCREEN)
    return width, height


def _get_virtual_screen() -> tuple[int, int, int, int]:
    """Get the virtual screen bounds (all monitors)."""
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return left, top, width, height


def _screen_to_absolute(x: int, y: int) -> tuple[int, int]:
    """
    Convert screen coordinates to absolute coordinates for SendInput.

    SendInput with MOUSEEVENTF_ABSOLUTE uses coordinates in the range [0, 65535].
    """
    vx, vy, vw, vh = _get_virtual_screen()

    # Normalize to virtual screen space
    abs_x = int(((x - vx) * 65536) / vw)
    abs_y = int(((y - vy) * 65536) / vh)

    return abs_x, abs_y


def _send_input(inputs: list[INPUT]) -> int:
    """
    Send input events using SendInput API.

    Args:
        inputs: List of INPUT structures.

    Returns:
        Number of events successfully injected.

    Raises:
        OSError: If SendInput fails.
    """
    if not inputs:
        return 0

    n_inputs = len(inputs)
    input_array = (INPUT * n_inputs)(*inputs)

    result = user32.SendInput(
        n_inputs,
        ctypes.pointer(input_array),
        ctypes.sizeof(INPUT),
    )

    if result != n_inputs:
        error_code = kernel32.GetLastError()
        logger.warning(
            "SendInput injected %d/%d events (error: %d)",
            result, n_inputs, error_code
        )

    return result


# =============================================================================
# Input Helper Functions
# =============================================================================

def _create_mouse_input(
    dx: int = 0,
    dy: int = 0,
    flags: int = 0,
    mouse_data: int = 0,
) -> INPUT:
    """Create a mouse INPUT structure."""
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.union.mi.dx = dx
    inp.union.mi.dy = dy
    inp.union.mi.mouseData = mouse_data
    inp.union.mi.dwFlags = flags
    inp.union.mi.time = 0
    inp.union.mi.dwExtraInfo = None
    return inp


def _create_keyboard_input(
    vk: int = 0,
    scan: int = 0,
    flags: int = 0,
) -> INPUT:
    """Create a keyboard INPUT structure."""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.wScan = scan
    inp.union.ki.dwFlags = flags
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = None
    return inp


def _get_vk_code(key: str) -> tuple[int, bool]:
    """
    Get the virtual key code for a key name or character.

    Args:
        key: Key name (e.g., "ctrl", "a", "F1") or single character.

    Returns:
        Tuple of (virtual_key_code, needs_shift).

    Raises:
        ValueError: If key is not recognized.
    """
    key_lower = key.lower()

    # Check named keys first
    if key_lower in KEY_NAME_MAP:
        return KEY_NAME_MAP[key_lower], False

    # Single character - letters
    if len(key) == 1:
        char = key.upper()
        if 'A' <= char <= 'Z':
            # Check if shift is needed for uppercase
            needs_shift = key.isupper()
            return ord(char), needs_shift

        # Numbers
        if '0' <= char <= '9':
            return ord(char), False

        # Use VkKeyScan for other characters
        result = user32.VkKeyScanW(ord(key))
        if result != -1:
            vk = result & 0xFF
            shift_state = (result >> 8) & 0xFF
            needs_shift = bool(shift_state & 0x01)
            return vk, needs_shift

    raise ValueError(f"Unknown key: {key}")


def _is_extended_key(vk: int) -> bool:
    """Check if a virtual key code is an extended key."""
    return vk in EXTENDED_KEYS


# =============================================================================
# Windows Input Controller Class
# =============================================================================

class WindowsInputController(InputControllerBase):
    """
    Win32 SendInput-based input controller.

    Provides mouse and keyboard input injection with DPI awareness.
    Thread-safe for use from async code.
    """

    def __init__(self, set_dpi_aware: bool = True):
        """
        Initialize the input controller.

        Args:
            set_dpi_aware: Whether to set DPI awareness for the process.
        """
        self._dpi_aware = False
        if set_dpi_aware:
            self._dpi_aware = _set_dpi_awareness()

        logger.info(
            "WindowsInputController initialized (DPI aware: %s)",
            self._dpi_aware
        )

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
            abs_x, abs_y = _screen_to_absolute(x, y)

            inp = _create_mouse_input(
                dx=abs_x,
                dy=abs_y,
                flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            )

            sent = _send_input([inp])
            return InputResult(success=sent > 0, events_sent=sent)

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
            # Get button flags
            button_lower = button.lower()
            if button_lower == "left":
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP
                mouse_data = 0
            elif button_lower == "right":
                down_flag = MOUSEEVENTF_RIGHTDOWN
                up_flag = MOUSEEVENTF_RIGHTUP
                mouse_data = 0
            elif button_lower == "middle":
                down_flag = MOUSEEVENTF_MIDDLEDOWN
                up_flag = MOUSEEVENTF_MIDDLEUP
                mouse_data = 0
            elif button_lower == "x1":
                down_flag = MOUSEEVENTF_XDOWN
                up_flag = MOUSEEVENTF_XUP
                mouse_data = XBUTTON1
            elif button_lower == "x2":
                down_flag = MOUSEEVENTF_XDOWN
                up_flag = MOUSEEVENTF_XUP
                mouse_data = XBUTTON2
            else:
                return InputResult(
                    success=False,
                    events_sent=0,
                    error=f"Unknown mouse button: {button}"
                )

            # Move to position first
            move_result = self.move(x, y)
            if not move_result.success:
                return move_result

            total_sent = move_result.events_sent

            # Perform click(s)
            for i in range(count):
                if i > 0:
                    time.sleep(self.DOUBLE_CLICK_DELAY)

                # Mouse down
                down_inp = _create_mouse_input(
                    flags=down_flag,
                    mouse_data=mouse_data,
                )
                sent = _send_input([down_inp])
                total_sent += sent

                time.sleep(self.CLICK_DELAY)

                # Mouse up
                up_inp = _create_mouse_input(
                    flags=up_flag,
                    mouse_data=mouse_data,
                )
                sent = _send_input([up_inp])
                total_sent += sent

            return InputResult(success=True, events_sent=total_sent)

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
            button_lower = button.lower()
            if button_lower == "left":
                down_flag = MOUSEEVENTF_LEFTDOWN
                up_flag = MOUSEEVENTF_LEFTUP
            elif button_lower == "right":
                down_flag = MOUSEEVENTF_RIGHTDOWN
                up_flag = MOUSEEVENTF_RIGHTUP
            elif button_lower == "middle":
                down_flag = MOUSEEVENTF_MIDDLEDOWN
                up_flag = MOUSEEVENTF_MIDDLEUP
            else:
                return InputResult(
                    success=False,
                    events_sent=0,
                    error=f"Unknown mouse button: {button}"
                )

            total_sent = 0
            step_delay = duration / steps if steps > 0 else 0

            # Move to start position
            move_result = self.move(start_x, start_y)
            if not move_result.success:
                return move_result
            total_sent += move_result.events_sent

            # Mouse down
            down_inp = _create_mouse_input(flags=down_flag)
            sent = _send_input([down_inp])
            total_sent += sent

            time.sleep(self.CLICK_DELAY)

            # Interpolate movement
            for i in range(1, steps + 1):
                t = i / steps
                cur_x = int(start_x + (end_x - start_x) * t)
                cur_y = int(start_y + (end_y - start_y) * t)

                move_result = self.move(cur_x, cur_y)
                total_sent += move_result.events_sent

                if step_delay > 0:
                    time.sleep(step_delay)

            # Mouse up
            up_inp = _create_mouse_input(flags=up_flag)
            sent = _send_input([up_inp])
            total_sent += sent

            return InputResult(success=True, events_sent=total_sent)

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
                   Each unit of WHEEL_DELTA (120) is one "click" of the wheel.
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

            # Create scroll input
            scroll_flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
            scroll_data = delta * WHEEL_DELTA

            inp = _create_mouse_input(
                flags=scroll_flag,
                mouse_data=scroll_data,
            )

            sent = _send_input([inp])
            total_sent += sent

            return InputResult(success=sent > 0, events_sent=total_sent)

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
            vk, _ = _get_vk_code(key)
            flags = KEYEVENTF_EXTENDEDKEY if _is_extended_key(vk) else 0

            inp = _create_keyboard_input(vk=vk, flags=flags)
            sent = _send_input([inp])

            return InputResult(success=sent > 0, events_sent=sent)

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
            vk, _ = _get_vk_code(key)
            flags = KEYEVENTF_KEYUP
            if _is_extended_key(vk):
                flags |= KEYEVENTF_EXTENDEDKEY

            inp = _create_keyboard_input(vk=vk, flags=flags)
            sent = _send_input([inp])

            return InputResult(success=sent > 0, events_sent=sent)

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
            vk, needs_shift = _get_vk_code(key)
            inputs: list[INPUT] = []

            # Add shift if needed
            if needs_shift:
                inputs.append(_create_keyboard_input(vk=VK.SHIFT))

            # Key down
            flags = KEYEVENTF_EXTENDEDKEY if _is_extended_key(vk) else 0
            inputs.append(_create_keyboard_input(vk=vk, flags=flags))

            # Key up
            up_flags = KEYEVENTF_KEYUP
            if _is_extended_key(vk):
                up_flags |= KEYEVENTF_EXTENDEDKEY
            inputs.append(_create_keyboard_input(vk=vk, flags=up_flags))

            # Release shift if it was pressed
            if needs_shift:
                inputs.append(_create_keyboard_input(vk=VK.SHIFT, flags=KEYEVENTF_KEYUP))

            sent = _send_input(inputs)
            return InputResult(success=sent == len(inputs), events_sent=sent)

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
            pressed_keys: list[tuple[int, int]] = []  # (vk, flags)

            # Press all keys down
            for key in keys:
                vk, _ = _get_vk_code(key)
                flags = KEYEVENTF_EXTENDEDKEY if _is_extended_key(vk) else 0

                inp = _create_keyboard_input(vk=vk, flags=flags)
                sent = _send_input([inp])
                total_sent += sent

                pressed_keys.append((vk, flags))
                time.sleep(self.KEY_CHORD_DELAY)

            time.sleep(self.KEY_PRESS_DELAY)

            # Release all keys in reverse order
            for vk, flags in reversed(pressed_keys):
                up_flags = KEYEVENTF_KEYUP
                if flags & KEYEVENTF_EXTENDEDKEY:
                    up_flags |= KEYEVENTF_EXTENDEDKEY

                inp = _create_keyboard_input(vk=vk, flags=up_flags)
                sent = _send_input([inp])
                total_sent += sent

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
            use_unicode: If True, use Unicode input (recommended for special chars).

        Returns:
            InputResult indicating success/failure.
        """
        if not text:
            return InputResult(success=True, events_sent=0)

        try:
            total_sent = 0
            actual_interval = interval if interval > 0 else self.TYPE_CHAR_DELAY

            for i, char in enumerate(text):
                if use_unicode:
                    # Use KEYEVENTF_UNICODE for direct Unicode input
                    scan_code = ord(char)

                    # Key down
                    down_inp = _create_keyboard_input(
                        scan=scan_code,
                        flags=KEYEVENTF_UNICODE,
                    )
                    sent = _send_input([down_inp])
                    total_sent += sent

                    # Key up
                    up_inp = _create_keyboard_input(
                        scan=scan_code,
                        flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    )
                    sent = _send_input([up_inp])
                    total_sent += sent

                else:
                    # Use VK codes (ASCII compatible only)
                    result = self.key_press(char)
                    total_sent += result.events_sent
                    if not result.success:
                        return InputResult(
                            success=False,
                            events_sent=total_sent,
                            error=f"Failed to type character: {char}"
                        )

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
        point = POINT()
        user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def get_screen_size(self) -> tuple[int, int]:
        """
        Get the primary screen size.

        Returns:
            Tuple of (width, height) in pixels.
        """
        return _get_screen_size()

    def get_virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        """
        Get the virtual screen bounds (all monitors).

        Returns:
            Tuple of (left, top, width, height).
        """
        return _get_virtual_screen()


# Alias for backwards compatibility
InputController = WindowsInputController
