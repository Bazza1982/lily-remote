"""Control module - Cross-platform input and screen control.

This module provides:
- input: Mouse and keyboard control (Win32 SendInput / pynput)
- verify: Read-back verification of input operations
- screen: Screenshot capture and streaming (mss)

Platform support:
- Windows: Full feature set with Win32 APIs
- Linux: pynput + Xlib for input/verification
- macOS: pynput (partial support)
"""

from . import input
from . import verify
from . import screen

__all__ = ["input", "verify", "screen"]
