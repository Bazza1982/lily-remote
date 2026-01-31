"""Base classes and data structures for verification.

Platform-independent definitions for command verification.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    """Information about a window."""
    hwnd: int  # Window handle (or ID on Linux)
    title: str
    class_name: str
    process_id: int
    rect: Optional[tuple[int, int, int, int]] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "process_id": self.process_id,
        }
        if self.rect:
            result["rect"] = {
                "left": self.rect[0],
                "top": self.rect[1],
                "right": self.rect[2],
                "bottom": self.rect[3],
            }
        return result


@dataclass
class VerificationState:
    """State captured for verification purposes."""
    timestamp: float
    cursor_x: int
    cursor_y: int
    foreground_window: Optional[WindowInfo] = None
    window_at_cursor: Optional[WindowInfo] = None
    focused_window: Optional[WindowInfo] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "timestamp": self.timestamp,
            "cursor_position": [self.cursor_x, self.cursor_y],
        }
        if self.foreground_window:
            result["foreground_window"] = self.foreground_window.to_dict()
        if self.window_at_cursor:
            result["window_at_cursor"] = self.window_at_cursor.to_dict()
        if self.focused_window:
            result["focused_window"] = self.focused_window.to_dict()
        return result


@dataclass
class VerificationResult:
    """Result of a verification check."""
    success: bool
    before: Optional[VerificationState] = None
    after: Optional[VerificationState] = None
    cursor_moved: bool = False
    cursor_delta: Optional[tuple[int, int]] = None
    foreground_changed: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "success": self.success,
            "cursor_moved": self.cursor_moved,
            "foreground_changed": self.foreground_changed,
        }
        if self.cursor_delta:
            result["cursor_delta"] = list(self.cursor_delta)
        if self.after:
            result["cursor_after"] = [self.after.cursor_x, self.after.cursor_y]
            if self.after.foreground_window:
                result["foreground_window"] = self.after.foreground_window.title
        if self.error:
            result["error"] = self.error
        return result


class VerifierBase(ABC):
    """Abstract base class for platform-specific verifiers."""

    @abstractmethod
    def get_cursor_position(self) -> tuple[int, int]:
        """Get current cursor position."""
        pass

    @abstractmethod
    def get_foreground_window_info(self) -> Optional[WindowInfo]:
        """Get information about the foreground window."""
        pass

    def capture_state(self, include_window_at_cursor: bool = False) -> VerificationState:
        """Capture current system state."""
        timestamp = time.time()
        cursor_x, cursor_y = self.get_cursor_position()
        foreground_window = self.get_foreground_window_info()

        return VerificationState(
            timestamp=timestamp,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            foreground_window=foreground_window,
            window_at_cursor=None,  # Optional, platform-specific
            focused_window=None,
        )

    def verify_cursor_position(
        self,
        expected_x: int,
        expected_y: int,
        tolerance: int = 5,
    ) -> VerificationResult:
        """Verify cursor is at expected position."""
        try:
            state = self.capture_state()
            delta_x = abs(state.cursor_x - expected_x)
            delta_y = abs(state.cursor_y - expected_y)
            success = delta_x <= tolerance and delta_y <= tolerance

            return VerificationResult(
                success=success,
                after=state,
                cursor_moved=True,
                cursor_delta=(state.cursor_x - expected_x, state.cursor_y - expected_y),
                error=None if success else f"Cursor at ({state.cursor_x}, {state.cursor_y}), expected ({expected_x}, {expected_y})",
            )
        except Exception as e:
            logger.error("Cursor verification failed: %s", e)
            return VerificationResult(success=False, error=str(e))

    def quick_verify(self) -> dict:
        """Quick verification snapshot."""
        cursor_x, cursor_y = self.get_cursor_position()
        fg_info = self.get_foreground_window_info()
        fg_title = fg_info.title if fg_info else ""

        return {
            "cursor_after": [cursor_x, cursor_y],
            "foreground_window": fg_title,
        }
