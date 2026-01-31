"""System tray application for Lily Remote Agent.

Cross-platform support:
- Windows: Full feature set including click-through overlay
- Linux/macOS: Overlay visible but not click-through
"""

import logging
import socket
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

from .security.pairing import PairingManager, PairedClient

logger = logging.getLogger(__name__)

# Try to import pystray - may fail on headless systems
PYSTRAY_AVAILABLE = False
try:
    import pystray
    PYSTRAY_AVAILABLE = True
except Exception as e:
    logger.warning(f"pystray not available (headless mode?): {e}")

# Platform detection
IS_WINDOWS = sys.platform == "win32"

# Windows API constants for overlay window (only used on Windows)
if IS_WINDOWS:
    import ctypes
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_POPUP = 0x80000000
    GWL_EXSTYLE = -20
    LWA_ALPHA = 0x00000002
    LWA_COLORKEY = 0x00000001
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
    HWND_TOPMOST = -1


class TrayIconColor(Enum):
    """Colors for different tray icon states."""
    IDLE = (128, 128, 128)       # Gray - no active connections
    CONNECTED = (0, 200, 0)      # Green - active session
    PAIRING = (255, 165, 0)      # Orange - pairing request pending
    ERROR = (200, 0, 0)          # Red - error state


@dataclass
class TrayState:
    """Current state of the tray application."""
    active_sessions: int = 0
    pending_pairing_requests: int = 0
    server_running: bool = False
    server_port: int = 8765
    error_message: Optional[str] = None
    controlling_client: Optional[str] = None  # Name of controlling client


class ControlIndicator:
    """
    On-screen indicator showing when remote control is active.

    Displays a semi-transparent overlay at the top of the screen
    showing "Being Controlled by [client_name]".
    """

    INDICATOR_HEIGHT = 32
    INDICATOR_BG_COLOR = (200, 50, 50)  # Red background
    INDICATOR_TEXT_COLOR = (255, 255, 255)  # White text
    INDICATOR_ALPHA = 200  # Semi-transparent (0-255)

    def __init__(self):
        """Initialize the control indicator."""
        self._window = None
        self._visible = False
        self._lock = threading.Lock()
        self._client_name: Optional[str] = None
        self._update_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def show(self, client_name: str) -> None:
        """
        Show the control indicator.

        Args:
            client_name: Name of the controlling client.
        """
        with self._lock:
            self._client_name = client_name
            if not self._visible:
                self._visible = True
                self._stop_event.clear()
                self._update_thread = threading.Thread(
                    target=self._run_indicator,
                    daemon=True,
                    name="control-indicator",
                )
                self._update_thread.start()

    def hide(self) -> None:
        """Hide the control indicator."""
        with self._lock:
            if self._visible:
                self._visible = False
                self._stop_event.set()
                self._client_name = None

    def update_client_name(self, client_name: str) -> None:
        """Update the displayed client name."""
        with self._lock:
            self._client_name = client_name

    def _run_indicator(self) -> None:
        """Run the indicator window (Windows-specific using tkinter)."""
        try:
            import tkinter as tk

            root = tk.Tk()
            root.title("Lily Remote - Being Controlled")

            # Get screen dimensions
            screen_width = root.winfo_screenwidth()

            # Configure window
            root.geometry(f"{screen_width}x{self.INDICATOR_HEIGHT}+0+0")
            root.overrideredirect(True)  # Remove window decorations
            root.attributes("-topmost", True)  # Always on top
            root.attributes("-alpha", self.INDICATOR_ALPHA / 255)  # Transparency

            # Make window click-through on Windows
            if IS_WINDOWS:
                try:
                    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
                    ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
                except Exception as e:
                    logger.debug(f"Could not set window style: {e}")
            # On Linux/macOS, the overlay is visible but not click-through

            # Create frame with colored background
            frame = tk.Frame(
                root,
                bg=f"#{self.INDICATOR_BG_COLOR[0]:02x}{self.INDICATOR_BG_COLOR[1]:02x}{self.INDICATOR_BG_COLOR[2]:02x}",
            )
            frame.pack(fill=tk.BOTH, expand=True)

            # Create label
            label = tk.Label(
                frame,
                text=self._get_indicator_text(),
                fg=f"#{self.INDICATOR_TEXT_COLOR[0]:02x}{self.INDICATOR_TEXT_COLOR[1]:02x}{self.INDICATOR_TEXT_COLOR[2]:02x}",
                bg=f"#{self.INDICATOR_BG_COLOR[0]:02x}{self.INDICATOR_BG_COLOR[1]:02x}{self.INDICATOR_BG_COLOR[2]:02x}",
                font=("Segoe UI", 12, "bold"),
            )
            label.pack(expand=True)

            def update_label():
                """Update the label text periodically."""
                if self._stop_event.is_set():
                    root.destroy()
                    return
                label.config(text=self._get_indicator_text())
                root.after(500, update_label)

            # Start update loop
            root.after(500, update_label)

            # Run the window
            root.mainloop()

        except Exception as e:
            logger.error(f"Control indicator error: {e}")
        finally:
            with self._lock:
                self._visible = False

    def _get_indicator_text(self) -> str:
        """Get the text to display on the indicator."""
        client = self._client_name or "Unknown"
        return f"🔴 BEING CONTROLLED BY: {client} 🔴"

    @property
    def is_visible(self) -> bool:
        """Check if the indicator is currently visible."""
        return self._visible


class SystemTray:
    """System tray application for managing the Lily Remote Agent."""

    ICON_SIZE = 64

    def __init__(
        self,
        pairing_manager: PairingManager,
        on_exit: Optional[Callable[[], None]] = None,
        on_kill_connections: Optional[Callable[[], None]] = None,
    ):
        """
        Initialize the system tray.

        Args:
            pairing_manager: The pairing manager instance.
            on_exit: Callback when exit is requested.
            on_kill_connections: Callback to kill all active connections.
        """
        self._pairing_manager = pairing_manager
        self._on_exit = on_exit
        self._on_kill_connections = on_kill_connections
        self._state = TrayState()
        self._icon: Optional[pystray.Icon] = None
        self._lock = threading.Lock()
        self._pairing_dialog_lock = threading.Lock()
        self._control_indicator = ControlIndicator()

        # Register the approval callback with the pairing manager
        self._pairing_manager.set_approval_callback(self._show_pairing_dialog)

    def _create_icon_image(self, color: tuple) -> Image.Image:
        """
        Create a tray icon image.

        Args:
            color: RGB tuple for the icon color.

        Returns:
            PIL Image for the tray icon.
        """
        image = Image.new("RGBA", (self.ICON_SIZE, self.ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        # Draw a filled circle
        margin = 4
        draw.ellipse(
            [margin, margin, self.ICON_SIZE - margin, self.ICON_SIZE - margin],
            fill=color + (255,),
            outline=(255, 255, 255, 200),
            width=2,
        )

        # Draw "L" letter in the center
        letter_color = (255, 255, 255, 255)
        center_x, center_y = self.ICON_SIZE // 2, self.ICON_SIZE // 2
        # Simple L shape
        draw.rectangle([center_x - 8, center_y - 10, center_x - 4, center_y + 8], fill=letter_color)
        draw.rectangle([center_x - 8, center_y + 4, center_x + 8, center_y + 8], fill=letter_color)

        return image

    def _get_current_icon_color(self) -> tuple:
        """Get the appropriate icon color based on current state."""
        if self._state.error_message:
            return TrayIconColor.ERROR.value
        if self._state.pending_pairing_requests > 0:
            return TrayIconColor.PAIRING.value
        if self._state.active_sessions > 0:
            return TrayIconColor.CONNECTED.value
        return TrayIconColor.IDLE.value

    def _update_icon(self) -> None:
        """Update the tray icon based on current state."""
        if self._icon is None:
            return

        with self._lock:
            color = self._get_current_icon_color()
            self._icon.icon = self._create_icon_image(color)
            self._icon.title = self._get_tooltip()

    def _get_tooltip(self) -> str:
        """Get the tooltip text for the tray icon."""
        hostname = socket.gethostname()
        status = "Running" if self._state.server_running else "Stopped"
        return f"Lily Remote - {hostname}\nStatus: {status}\nPort: {self._state.server_port}"

    def _get_status_text(self) -> str:
        """Get status text for the status menu item."""
        with self._lock:
            parts = []
            if self._state.error_message:
                parts.append(f"Error: {self._state.error_message}")
            else:
                status = "Running" if self._state.server_running else "Stopped"
                parts.append(f"Server: {status}")

            parts.append(f"Port: {self._state.server_port}")
            parts.append(f"Active sessions: {self._state.active_sessions}")

            pending = len(self._pairing_manager.get_pending_requests())
            if pending > 0:
                parts.append(f"Pending pairing requests: {pending}")

            return " | ".join(parts)

    def _build_paired_clients_menu(self) -> list:
        """Build submenu items for paired clients."""
        clients = self._pairing_manager.get_paired_clients()

        if not clients:
            return [pystray.MenuItem("No paired clients", None, enabled=False)]

        items = []
        for client in clients:
            # Create submenu for each client with unpair option
            client_menu = pystray.Menu(
                pystray.MenuItem(
                    f"ID: {client.client_id[:16]}...",
                    None,
                    enabled=False,
                ),
                pystray.MenuItem(
                    f"Paired: {self._format_timestamp(client.paired_at)}",
                    None,
                    enabled=False,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Unpair",
                    lambda _, cid=client.client_id: self._unpair_client(cid),
                ),
            )
            items.append(pystray.MenuItem(client.client_name, client_menu))

        return items

    def _format_timestamp(self, timestamp: float) -> str:
        """Format a Unix timestamp as a human-readable string."""
        from datetime import datetime
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M")

    def _unpair_client(self, client_id: str) -> None:
        """Unpair a client."""
        logger.info(f"Unpairing client: {client_id}")
        self._pairing_manager.unpair_client(client_id)
        self._update_icon()

    def _on_status_click(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """Handle status menu item click."""
        pass  # Status is display-only

    def _on_kill_connections_click(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """Handle kill all connections menu item click."""
        logger.info("Kill all connections requested from tray")
        if self._on_kill_connections:
            try:
                self._on_kill_connections()
            except Exception as e:
                logger.error(f"Error killing connections: {e}")
                self.set_error(str(e))
        self._update_icon()

    def _on_exit_click(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        """Handle exit menu item click."""
        logger.info("Exit requested from tray")
        if self._on_exit:
            try:
                self._on_exit()
            except Exception as e:
                logger.error(f"Error during exit callback: {e}")
        self.stop()

    def _create_menu(self) -> pystray.Menu:
        """Create the tray menu."""
        return pystray.Menu(
            pystray.MenuItem(
                lambda text: self._get_status_text(),
                self._on_status_click,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Paired Clients",
                pystray.Menu(lambda: self._build_paired_clients_menu()),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Kill All Connections",
                self._on_kill_connections_click,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._on_exit_click),
        )

    def _show_pairing_dialog(self, client_name: str, client_id: str) -> bool:
        """
        Show a pairing approval dialog.

        This method is called by the PairingManager when a client requests pairing.

        Args:
            client_name: Human-readable name of the client.
            client_id: Unique ID of the client.

        Returns:
            True if approved, False if rejected.
        """
        with self._pairing_dialog_lock:
            logger.info(f"Pairing request from: {client_name} ({client_id})")

            # Update state to show pending pairing
            with self._lock:
                self._state.pending_pairing_requests += 1
            self._update_icon()

            try:
                # Use tkinter for a simple dialog
                # This runs in a separate thread, so we need to handle threading carefully
                import tkinter as tk
                from tkinter import messagebox

                # Create a hidden root window
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)

                result = messagebox.askyesno(
                    "Lily Remote - Pairing Request",
                    f"A client wants to pair with this computer:\n\n"
                    f"Name: {client_name}\n"
                    f"ID: {client_id[:32]}...\n\n"
                    f"Do you want to approve this pairing request?",
                    icon="question",
                    parent=root,
                )

                root.destroy()

                if result:
                    logger.info(f"Pairing approved for: {client_name}")
                else:
                    logger.info(f"Pairing rejected for: {client_name}")

                return result

            except Exception as e:
                logger.error(f"Error showing pairing dialog: {e}")
                # Fail-safe: reject if dialog fails
                return False

            finally:
                with self._lock:
                    self._state.pending_pairing_requests = max(
                        0, self._state.pending_pairing_requests - 1
                    )
                self._update_icon()

    def update_state(
        self,
        server_running: Optional[bool] = None,
        server_port: Optional[int] = None,
        active_sessions: Optional[int] = None,
        controlling_client: Optional[str] = None,
    ) -> None:
        """
        Update the tray state.

        Args:
            server_running: Whether the server is running.
            server_port: The server port.
            active_sessions: Number of active sessions.
            controlling_client: Name of the controlling client (or None to clear).
        """
        with self._lock:
            if server_running is not None:
                self._state.server_running = server_running
            if server_port is not None:
                self._state.server_port = server_port
            if active_sessions is not None:
                old_sessions = self._state.active_sessions
                self._state.active_sessions = active_sessions

                # Show/hide control indicator based on session count
                if active_sessions > 0 and old_sessions == 0:
                    # Session started - show indicator
                    client_name = controlling_client or self._state.controlling_client or "Remote Client"
                    self._control_indicator.show(client_name)
                elif active_sessions == 0 and old_sessions > 0:
                    # All sessions ended - hide indicator
                    self._control_indicator.hide()

            if controlling_client is not None:
                self._state.controlling_client = controlling_client
                if self._state.active_sessions > 0:
                    self._control_indicator.update_client_name(controlling_client)

        self._update_icon()

    def set_controlling_client(self, client_name: Optional[str]) -> None:
        """
        Set the name of the controlling client.

        Args:
            client_name: Name of the controlling client, or None if no active control.
        """
        with self._lock:
            self._state.controlling_client = client_name
            if client_name and self._state.active_sessions > 0:
                self._control_indicator.update_client_name(client_name)
            elif not client_name:
                self._control_indicator.hide()

    def set_error(self, message: Optional[str]) -> None:
        """
        Set or clear an error message.

        Args:
            message: The error message, or None to clear.
        """
        with self._lock:
            self._state.error_message = message
        self._update_icon()

    def clear_error(self) -> None:
        """Clear any error message."""
        self.set_error(None)

    def run(self) -> None:
        """Run the system tray (blocking)."""
        logger.info("Starting system tray")

        initial_color = self._get_current_icon_color()
        self._icon = pystray.Icon(
            "lily-remote",
            self._create_icon_image(initial_color),
            "Lily Remote Agent",
            menu=self._create_menu(),
        )

        try:
            self._icon.run()
        except Exception as e:
            logger.error(f"Tray error: {e}")
            raise

    def run_detached(self) -> None:
        """Run the system tray in detached mode (non-blocking for some backends)."""
        logger.info("Starting system tray (detached)")

        initial_color = self._get_current_icon_color()
        self._icon = pystray.Icon(
            "lily-remote",
            self._create_icon_image(initial_color),
            "Lily Remote Agent",
            menu=self._create_menu(),
        )

        try:
            self._icon.run_detached()
        except Exception as e:
            logger.error(f"Tray error: {e}")
            raise

    def stop(self) -> None:
        """Stop the system tray."""
        logger.info("Stopping system tray")

        # Hide control indicator
        self._control_indicator.hide()

        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception as e:
                logger.error(f"Error stopping tray icon: {e}")
            self._icon = None

    @property
    def is_running(self) -> bool:
        """Check if the tray is running."""
        return self._icon is not None
