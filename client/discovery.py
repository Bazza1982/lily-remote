"""LAN scanning and agent discovery for Lily Remote Client.

Uses mDNS/Zeroconf to discover available Lily Remote agents on the network.
"""

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from zeroconf import IPVersion, ServiceBrowser, ServiceListener, Zeroconf

logger = logging.getLogger(__name__)

# Service type must match the agent's advertised service
SERVICE_TYPE = "_lilyremote._tcp.local."


@dataclass
class DiscoveredAgent:
    """Represents a discovered Lily Remote agent on the network."""

    name: str
    hostname: str
    addresses: list[str]
    port: int
    version: str
    properties: dict[str, str] = field(default_factory=dict)
    discovered_at: float = field(default_factory=time.time)

    @property
    def primary_address(self) -> str:
        """Get the primary (first) IP address."""
        return self.addresses[0] if self.addresses else ""

    @property
    def url(self) -> str:
        """Get the base URL for connecting to this agent."""
        if not self.primary_address:
            return ""
        return f"https://{self.primary_address}:{self.port}"

    @property
    def websocket_url(self) -> str:
        """Get the WebSocket URL for the events endpoint."""
        if not self.primary_address:
            return ""
        return f"wss://{self.primary_address}:{self.port}/events"


class AgentDiscoveryListener(ServiceListener):
    """
    Listener for mDNS service discovery events.

    Tracks discovered agents and notifies callbacks when agents
    are added, updated, or removed from the network.
    """

    def __init__(
        self,
        on_agent_found: Optional[Callable[[DiscoveredAgent], None]] = None,
        on_agent_removed: Optional[Callable[[str], None]] = None,
        on_agent_updated: Optional[Callable[[DiscoveredAgent], None]] = None,
    ):
        """
        Initialize the discovery listener.

        Args:
            on_agent_found: Callback when a new agent is discovered.
            on_agent_removed: Callback when an agent is removed (by name).
            on_agent_updated: Callback when an agent's info is updated.
        """
        self._agents: dict[str, DiscoveredAgent] = {}
        self._lock = threading.Lock()
        self._on_agent_found = on_agent_found
        self._on_agent_removed = on_agent_removed
        self._on_agent_updated = on_agent_updated

    @property
    def agents(self) -> list[DiscoveredAgent]:
        """Get a copy of all currently discovered agents."""
        with self._lock:
            return list(self._agents.values())

    def get_agent(self, name: str) -> Optional[DiscoveredAgent]:
        """Get a specific agent by name."""
        with self._lock:
            return self._agents.get(name)

    def _parse_service_info(
        self,
        zc: Zeroconf,
        service_type: str,
        name: str,
    ) -> Optional[DiscoveredAgent]:
        """Parse service info into a DiscoveredAgent."""
        info = zc.get_service_info(service_type, name)
        if not info:
            logger.debug("Could not get service info for %s", name)
            return None

        # Parse addresses
        addresses = []
        for addr in info.addresses:
            try:
                ip = socket.inet_ntoa(addr)
                addresses.append(ip)
            except Exception:
                pass

        if not addresses:
            logger.debug("No valid addresses for %s", name)
            return None

        # Parse properties
        properties = {}
        if info.properties:
            for key, value in info.properties.items():
                try:
                    k = key.decode("utf-8") if isinstance(key, bytes) else key
                    v = value.decode("utf-8") if isinstance(value, bytes) else str(value)
                    properties[k] = v
                except Exception:
                    pass

        # Extract version from properties
        version = properties.get("version", "unknown")

        # Extract hostname from properties or server name
        hostname = properties.get("hostname", "")
        if not hostname and info.server:
            # Remove .local. suffix
            hostname = info.server.rstrip(".")
            if hostname.endswith(".local"):
                hostname = hostname[:-6]

        return DiscoveredAgent(
            name=name,
            hostname=hostname,
            addresses=addresses,
            port=info.port,
            version=version,
            properties=properties,
        )

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        """Called when a new service is discovered."""
        logger.debug("Service found: %s", name)

        agent = self._parse_service_info(zc, service_type, name)
        if not agent:
            return

        with self._lock:
            self._agents[name] = agent

        logger.info(
            "Discovered agent: %s at %s:%d",
            agent.hostname,
            agent.primary_address,
            agent.port,
        )

        if self._on_agent_found:
            try:
                self._on_agent_found(agent)
            except Exception as e:
                logger.warning("Error in on_agent_found callback: %s", e)

    def update_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        """Called when a service is updated."""
        logger.debug("Service updated: %s", name)

        agent = self._parse_service_info(zc, service_type, name)
        if not agent:
            return

        with self._lock:
            self._agents[name] = agent

        logger.debug(
            "Updated agent: %s at %s:%d",
            agent.hostname,
            agent.primary_address,
            agent.port,
        )

        if self._on_agent_updated:
            try:
                self._on_agent_updated(agent)
            except Exception as e:
                logger.warning("Error in on_agent_updated callback: %s", e)

    def remove_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        """Called when a service is removed."""
        logger.debug("Service removed: %s", name)

        with self._lock:
            if name in self._agents:
                del self._agents[name]

        logger.info("Agent removed: %s", name)

        if self._on_agent_removed:
            try:
                self._on_agent_removed(name)
            except Exception as e:
                logger.warning("Error in on_agent_removed callback: %s", e)


class AgentScanner:
    """
    Scanner for discovering Lily Remote agents on the local network.

    Provides both synchronous (blocking) and asynchronous (callback-based)
    discovery modes.
    """

    def __init__(self):
        """Initialize the agent scanner."""
        self._zeroconf: Optional[Zeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._listener: Optional[AgentDiscoveryListener] = None
        self._is_scanning = False

    @property
    def is_scanning(self) -> bool:
        """Check if the scanner is currently active."""
        return self._is_scanning

    @property
    def discovered_agents(self) -> list[DiscoveredAgent]:
        """Get all currently discovered agents."""
        if self._listener:
            return self._listener.agents
        return []

    def scan(
        self,
        timeout: float = 3.0,
        on_agent_found: Optional[Callable[[DiscoveredAgent], None]] = None,
    ) -> list[DiscoveredAgent]:
        """
        Perform a blocking scan for agents on the network.

        This method blocks for the specified timeout while collecting
        agent discovery events, then returns all found agents.

        Args:
            timeout: How long to scan in seconds.
            on_agent_found: Optional callback for each agent as discovered.

        Returns:
            List of discovered agents.
        """
        if self._is_scanning:
            logger.warning("Scan already in progress")
            return []

        discovered: list[DiscoveredAgent] = []
        lock = threading.Lock()

        def on_found(agent: DiscoveredAgent) -> None:
            with lock:
                # Avoid duplicates by name
                if not any(a.name == agent.name for a in discovered):
                    discovered.append(agent)
                    if on_agent_found:
                        on_agent_found(agent)

        try:
            self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
            self._listener = AgentDiscoveryListener(on_agent_found=on_found)
            self._browser = ServiceBrowser(
                self._zeroconf,
                SERVICE_TYPE,
                self._listener,
            )
            self._is_scanning = True

            logger.debug("Scanning for agents (timeout: %.1fs)", timeout)
            time.sleep(timeout)

        except Exception as e:
            logger.error("Error during scan: %s", e)
        finally:
            self._stop_browser()

        logger.info("Scan complete: found %d agent(s)", len(discovered))
        return discovered

    def start_continuous_scan(
        self,
        on_agent_found: Optional[Callable[[DiscoveredAgent], None]] = None,
        on_agent_removed: Optional[Callable[[str], None]] = None,
        on_agent_updated: Optional[Callable[[DiscoveredAgent], None]] = None,
    ) -> bool:
        """
        Start a continuous background scan for agents.

        This method returns immediately. Use stop_continuous_scan() to stop.
        Access discovered agents via the discovered_agents property.

        Args:
            on_agent_found: Callback when a new agent is discovered.
            on_agent_removed: Callback when an agent is removed.
            on_agent_updated: Callback when an agent is updated.

        Returns:
            True if scanning started successfully, False otherwise.
        """
        if self._is_scanning:
            logger.warning("Continuous scan already in progress")
            return True

        try:
            self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
            self._listener = AgentDiscoveryListener(
                on_agent_found=on_agent_found,
                on_agent_removed=on_agent_removed,
                on_agent_updated=on_agent_updated,
            )
            self._browser = ServiceBrowser(
                self._zeroconf,
                SERVICE_TYPE,
                self._listener,
            )
            self._is_scanning = True

            logger.info("Continuous agent scanning started")
            return True

        except Exception as e:
            logger.error("Failed to start continuous scan: %s", e)
            self._stop_browser()
            return False

    def stop_continuous_scan(self) -> None:
        """Stop the continuous background scan."""
        if not self._is_scanning:
            return

        logger.info("Stopping continuous agent scan")
        self._stop_browser()

    def _stop_browser(self) -> None:
        """Stop the browser and clean up resources."""
        if self._browser:
            try:
                self._browser.cancel()
            except Exception as e:
                logger.warning("Error canceling browser: %s", e)

        if self._zeroconf:
            try:
                self._zeroconf.close()
            except Exception as e:
                logger.warning("Error closing Zeroconf: %s", e)

        self._browser = None
        self._zeroconf = None
        self._listener = None
        self._is_scanning = False

    def __enter__(self) -> "AgentScanner":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - stops scanning."""
        self.stop_continuous_scan()


def discover_agents(
    timeout: float = 3.0,
    on_agent_found: Optional[Callable[[DiscoveredAgent], None]] = None,
) -> list[DiscoveredAgent]:
    """
    Convenience function to discover agents on the network.

    This is a blocking call that scans for the specified timeout.

    Args:
        timeout: How long to scan in seconds.
        on_agent_found: Optional callback for each agent as discovered.

    Returns:
        List of discovered agents.
    """
    scanner = AgentScanner()
    return scanner.scan(timeout=timeout, on_agent_found=on_agent_found)


def discover_first_agent(timeout: float = 5.0) -> Optional[DiscoveredAgent]:
    """
    Convenience function to discover the first available agent.

    Returns as soon as an agent is found, or after timeout.

    Args:
        timeout: Maximum time to wait in seconds.

    Returns:
        The first discovered agent, or None if none found.
    """
    result: list[DiscoveredAgent] = []
    found_event = threading.Event()

    def on_found(agent: DiscoveredAgent) -> None:
        if not result:
            result.append(agent)
            found_event.set()

    scanner = AgentScanner()

    try:
        scanner._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        scanner._listener = AgentDiscoveryListener(on_agent_found=on_found)
        scanner._browser = ServiceBrowser(
            scanner._zeroconf,
            SERVICE_TYPE,
            scanner._listener,
        )
        scanner._is_scanning = True

        # Wait for first agent or timeout
        found_event.wait(timeout=timeout)

    except Exception as e:
        logger.error("Error during discovery: %s", e)
    finally:
        scanner._stop_browser()

    return result[0] if result else None
