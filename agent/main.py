"""Entry point for Lily Remote Agent."""

import argparse
import logging
import signal
import socket
import sys
import threading
from pathlib import Path
from typing import Optional

import uvicorn

from .api.server import create_app
from .security.pairing import PairingManager
from .security.tls import load_or_generate_cert

# SystemTray is imported conditionally (may fail on headless systems)
SystemTray = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


class AgentApplication:
    """Main application coordinator for the Lily Remote Agent."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        config_path: Optional[Path] = None,
        no_tray: bool = False,
    ):
        """
        Initialize the agent application.

        Args:
            host: Host to bind the server to.
            port: Port to bind the server to.
            config_path: Optional path to configuration file.
            no_tray: If True, run without system tray (headless mode).
        """
        self._host = host
        self._port = port
        self._config_path = config_path
        self._no_tray = no_tray

        # Components
        self._pairing_manager: Optional[PairingManager] = None
        self._tray: Optional[SystemTray] = None
        self._server_thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

        # State
        self._shutdown_event = threading.Event()
        self._active_sessions: set[str] = set()
        self._sessions_lock = threading.Lock()

    def _load_config(self) -> dict:
        """Load configuration from file."""
        config = {
            "server": {"host": self._host, "port": self._port},
            "security": {"pairing_timeout_seconds": 60},
        }

        if self._config_path and self._config_path.exists():
            try:
                import yaml
                with open(self._config_path) as f:
                    file_config = yaml.safe_load(f)
                    if file_config:
                        # Merge configurations
                        for key, value in file_config.items():
                            if key in config and isinstance(config[key], dict):
                                config[key].update(value)
                            else:
                                config[key] = value
                logger.info(f"Loaded configuration from {self._config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config file: {e}")

        return config

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown")
            self.shutdown()

        # Handle SIGINT (Ctrl+C) and SIGTERM
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def _kill_all_connections(self) -> None:
        """Kill all active connections."""
        logger.info("Killing all active connections")
        with self._sessions_lock:
            session_ids = list(self._active_sessions)
            self._active_sessions.clear()

        # Log the terminated sessions
        for session_id in session_ids:
            logger.info(f"Terminated session: {session_id}")

        # Update tray state
        if self._tray:
            self._tray.update_state(active_sessions=0)

    def _run_server(self) -> None:
        """Run the FastAPI server in a thread."""
        logger.info(f"Starting server on {self._host}:{self._port}")

        try:
            # Get or generate TLS certificates
            hostname = socket.gethostname()
            cert_path, key_path = load_or_generate_cert(hostname)
            logger.info(f"Using TLS certificate: {cert_path}")

            # Create the FastAPI app
            app = create_app(self._pairing_manager)

            # Configure uvicorn
            config = uvicorn.Config(
                app=app,
                host=self._host,
                port=self._port,
                ssl_certfile=str(cert_path),
                ssl_keyfile=str(key_path),
                log_level="info",
                access_log=True,
            )

            self._uvicorn_server = uvicorn.Server(config)

            # Update tray state
            if self._tray:
                self._tray.update_state(server_running=True, server_port=self._port)
                self._tray.clear_error()

            # Run the server (blocking within this thread)
            self._uvicorn_server.run()

        except Exception as e:
            logger.error(f"Server error: {e}")
            if self._tray:
                self._tray.set_error(f"Server error: {e}")
                self._tray.update_state(server_running=False)

        finally:
            logger.info("Server thread exiting")
            if self._tray:
                self._tray.update_state(server_running=False)

    def _run_tray(self) -> None:
        """Run the system tray (blocking)."""
        logger.info("Starting system tray")
        try:
            self._tray.run()
        except Exception as e:
            logger.error(f"Tray error: {e}")
        finally:
            logger.info("Tray thread exiting")
            # If tray exits, trigger shutdown
            self.shutdown()

    def run(self) -> None:
        """Run the agent application."""
        logger.info("=" * 50)
        logger.info("Lily Remote Agent starting")
        logger.info("=" * 50)

        try:
            # Load configuration
            config = self._load_config()
            self._host = config["server"].get("host", self._host)
            self._port = config["server"].get("port", self._port)

            # Set up signal handlers
            self._setup_signal_handlers()

            # Initialize the pairing manager
            self._pairing_manager = PairingManager()
            logger.info("Pairing manager initialized")

            # Initialize the system tray (skip in headless mode)
            if not self._no_tray:
                global SystemTray
                try:
                    from .tray import SystemTray
                    self._tray = SystemTray(
                        pairing_manager=self._pairing_manager,
                        on_exit=self.shutdown,
                        on_kill_connections=self._kill_all_connections,
                    )
                    logger.info("System tray initialized")
                except Exception as e:
                    logger.warning(f"Could not initialize system tray: {e}")
                    self._no_tray = True  # Fall back to headless mode
            else:
                logger.info("Running in headless mode (tray disabled)")

            # Start the server in a background thread
            self._server_thread = threading.Thread(
                target=self._run_server,
                name="uvicorn-server",
                daemon=True,
            )
            self._server_thread.start()
            logger.info("Server thread started")

            if self._no_tray:
                # Headless mode: wait for shutdown signal
                logger.info("Running in headless mode (no system tray)")
                try:
                    self._shutdown_event.wait()
                except KeyboardInterrupt:
                    pass
            else:
                # Run the tray in the main thread (blocking)
                # This is required because pystray (and most GUI toolkits)
                # need to run in the main thread on some platforms
                self._run_tray()

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error(f"Application error: {e}", exc_info=True)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Shut down the agent application."""
        if self._shutdown_event.is_set():
            return  # Already shutting down

        self._shutdown_event.set()
        logger.info("Shutting down Lily Remote Agent")

        # Stop the uvicorn server
        if self._uvicorn_server:
            logger.info("Stopping server...")
            self._uvicorn_server.should_exit = True

        # Stop the system tray
        if self._tray:
            logger.info("Stopping tray...")
            self._tray.stop()

        # Wait for server thread to finish
        if self._server_thread and self._server_thread.is_alive():
            logger.info("Waiting for server thread...")
            self._server_thread.join(timeout=5.0)
            if self._server_thread.is_alive():
                logger.warning("Server thread did not exit cleanly")

        logger.info("Shutdown complete")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Lily Remote Agent - Remote PC control for AI systems",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the server to",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to bind the server to",
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to configuration file",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Run without system tray (headless mode)",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Configure logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")

    # Use default config if not specified
    config_path = args.config
    if config_path is None:
        default_config = Path(__file__).parent / "config.yaml"
        if default_config.exists():
            config_path = default_config

    # Create and run the application
    app = AgentApplication(
        host=args.host,
        port=args.port,
        config_path=config_path,
        no_tray=args.no_tray,
    )

    try:
        app.run()
        return 0
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
