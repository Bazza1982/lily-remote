"""Cross-platform verification for command execution.

Verifies that input commands had the expected effect by capturing
cursor position and foreground window state after each action.

Automatically selects the appropriate platform implementation.
"""

import logging
import sys
import time
from typing import Optional

from .verify_base import (
    VerifierBase,
    WindowInfo,
    VerificationState,
    VerificationResult,
)

logger = logging.getLogger(__name__)

# Determine platform
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

# Select platform-specific implementation
_verifier: Optional[VerifierBase] = None

if IS_WINDOWS:
    from .verify_windows import WindowsVerifier
    _verifier = WindowsVerifier()
    logger.debug("Using Windows verifier")
elif IS_LINUX or IS_MACOS:
    try:
        from .verify_linux import LinuxVerifier
        _verifier = LinuxVerifier()
        logger.debug("Using Linux verifier")
    except ImportError as e:
        logger.warning("Linux verifier not available: %s", e)


def get_verifier() -> Optional[VerifierBase]:
    """Get the platform-specific verifier instance."""
    return _verifier


# Re-export types
__all__ = [
    "WindowInfo",
    "VerificationState",
    "VerificationResult",
    "get_verifier",
    "capture_state",
    "verify_cursor_position",
    "verify_foreground_window",
    "get_cursor_position",
    "get_foreground_window_title",
    "get_foreground_window_info",
    "quick_verify",
    "InputVerifier",
]


# =============================================================================
# Convenience Functions (delegate to verifier)
# =============================================================================

def capture_state(include_window_at_cursor: bool = False) -> VerificationState:
    """Capture the current system state for verification."""
    if _verifier:
        return _verifier.capture_state(include_window_at_cursor)
    return VerificationState(
        timestamp=time.time(),
        cursor_x=0,
        cursor_y=0,
    )


def verify_cursor_position(
    expected_x: int,
    expected_y: int,
    tolerance: int = 5,
) -> VerificationResult:
    """Verify that the cursor is at the expected position."""
    if _verifier:
        return _verifier.verify_cursor_position(expected_x, expected_y, tolerance)
    return VerificationResult(success=False, error="No verifier available")


def verify_foreground_window(
    expected_title: Optional[str] = None,
    expected_class: Optional[str] = None,
    partial_match: bool = True,
) -> VerificationResult:
    """Verify that the foreground window matches expectations."""
    if not _verifier:
        return VerificationResult(success=False, error="No verifier available")

    try:
        state = capture_state()

        if not state.foreground_window:
            return VerificationResult(
                success=False,
                after=state,
                error="No foreground window found",
            )

        # Check title
        if expected_title is not None:
            actual_title = state.foreground_window.title
            if partial_match:
                title_match = expected_title.lower() in actual_title.lower()
            else:
                title_match = expected_title == actual_title

            if not title_match:
                return VerificationResult(
                    success=False,
                    after=state,
                    error=f"Window title mismatch: expected '{expected_title}', got '{actual_title}'",
                )

        # Check class
        if expected_class is not None:
            actual_class = state.foreground_window.class_name
            if expected_class != actual_class:
                return VerificationResult(
                    success=False,
                    after=state,
                    error=f"Window class mismatch: expected '{expected_class}', got '{actual_class}'",
                )

        return VerificationResult(success=True, after=state)

    except Exception as e:
        logger.error("Foreground window verification failed: %s", e)
        return VerificationResult(success=False, error=str(e))


def get_cursor_position() -> tuple[int, int]:
    """Get the current cursor position."""
    if _verifier:
        return _verifier.get_cursor_position()
    return 0, 0


def get_foreground_window_title() -> str:
    """Get the title of the current foreground window."""
    if _verifier:
        info = _verifier.get_foreground_window_info()
        return info.title if info else ""
    return ""


def get_foreground_window_info() -> Optional[WindowInfo]:
    """Get detailed info about the foreground window."""
    if _verifier:
        return _verifier.get_foreground_window_info()
    return None


def quick_verify() -> dict:
    """Quick verification snapshot for command results."""
    if _verifier:
        return _verifier.quick_verify()
    return {"cursor_after": [0, 0], "foreground_window": ""}


# =============================================================================
# Verification Context Manager
# =============================================================================

class InputVerifier:
    """Context manager for verifying input operations."""

    def __init__(self, include_window_at_cursor: bool = False):
        self._include_window_at_cursor = include_window_at_cursor
        self._before: Optional[VerificationState] = None
        self._after: Optional[VerificationState] = None

    def capture_before(self) -> VerificationState:
        """Capture state before input operation."""
        self._before = capture_state(self._include_window_at_cursor)
        return self._before

    def capture_after(self) -> VerificationState:
        """Capture state after input operation."""
        self._after = capture_state(self._include_window_at_cursor)
        return self._after

    def verify_after(
        self,
        expected_cursor: Optional[tuple[int, int]] = None,
        cursor_tolerance: int = 5,
        expect_foreground_change: bool = False,
        expected_title: Optional[str] = None,
    ) -> VerificationResult:
        """Verify the state after an input operation."""
        if self._before is None:
            return VerificationResult(
                success=False,
                error="No 'before' state captured. Call capture_before() first.",
            )

        self._after = capture_state(self._include_window_at_cursor)

        # Check cursor movement
        cursor_moved = (
            self._after.cursor_x != self._before.cursor_x or
            self._after.cursor_y != self._before.cursor_y
        )

        cursor_delta = (
            self._after.cursor_x - self._before.cursor_x,
            self._after.cursor_y - self._before.cursor_y,
        )

        # Check foreground change
        before_hwnd = self._before.foreground_window.hwnd if self._before.foreground_window else 0
        after_hwnd = self._after.foreground_window.hwnd if self._after.foreground_window else 0
        foreground_changed = before_hwnd != after_hwnd

        # Validate expected cursor position
        cursor_valid = True
        cursor_error = None
        if expected_cursor is not None:
            delta_x = abs(self._after.cursor_x - expected_cursor[0])
            delta_y = abs(self._after.cursor_y - expected_cursor[1])
            cursor_valid = delta_x <= cursor_tolerance and delta_y <= cursor_tolerance
            if not cursor_valid:
                cursor_error = (
                    f"Cursor at ({self._after.cursor_x}, {self._after.cursor_y}), "
                    f"expected ({expected_cursor[0]}, {expected_cursor[1]})"
                )

        # Validate foreground change expectation
        foreground_valid = True
        foreground_error = None
        if expect_foreground_change and not foreground_changed:
            foreground_valid = False
            foreground_error = "Foreground window did not change as expected"

        # Validate expected title
        title_valid = True
        title_error = None
        if expected_title is not None and self._after.foreground_window:
            actual_title = self._after.foreground_window.title
            if expected_title.lower() not in actual_title.lower():
                title_valid = False
                title_error = f"Window title mismatch: expected '{expected_title}', got '{actual_title}'"

        # Combine validation results
        success = cursor_valid and foreground_valid and title_valid
        error = cursor_error or foreground_error or title_error

        return VerificationResult(
            success=success,
            before=self._before,
            after=self._after,
            cursor_moved=cursor_moved,
            cursor_delta=cursor_delta,
            foreground_changed=foreground_changed,
            error=error,
        )

    def get_summary(self) -> dict:
        """Get a summary of the verification for API responses."""
        if self._after is None:
            self._after = capture_state(self._include_window_at_cursor)

        result = {"cursor_after": [self._after.cursor_x, self._after.cursor_y]}
        if self._after.foreground_window:
            result["foreground_window"] = self._after.foreground_window.title
        return result
