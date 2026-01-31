"""mDNS/Zeroconf service registration for Lily Remote Agent.

Allows the agent to be discovered on the local network by advertising
the service via mDNS (Multicast DNS) / DNS-SD.
"""

import logging
import socket
from dataclasses import dataclass
from typing import Optional

from zeroconf import IPVersion, ServiceInfo, Zeroconf

logger = logging.getLogger(__name__)


@dataclass
class ServiceConfig:
    """Configuration for mDNS service advertisement."""

    service_type: str = "_lilyremote._tcp.local."
    service_name: str = "Lily Remote Agent"
    port: int = 8765
    properties: Optional[dict[str, str]] = None

    def __post_init__(self):
        if self.properties is None:
            self.properties = {}


class MDNSService:
    """
    mDNS service for advertising Lily Remote Agent on the local network.

    Uses zeroconf to register a DNS-SD service that clients can discover.
    The service advertises the hostname, port, and optional properties.
    """

    def __init__(self, config: Optional[ServiceConfig] = None):
        """
        Initialize the mDNS service.

        Args:
            config: Service configuration. Uses defaults if not provided.
        """
        self._config = config or ServiceConfig()
        self._zeroconf: Optional[Zeroconf] = None
        self._service_info: Optional[ServiceInfo] = None
        self._is_advertising = False

    @property
    def is_advertising(self) -> bool:
        """Check if the service is currently being advertised."""
        return self._is_advertising

    def _get_local_ip_addresses(self) -> list[str]:
        """
        Get all local IP addresses for this machine.

        Returns:
            List of IP addresses as strings.
        """
        addresses = []
        hostname = socket.gethostname()

        try:
            # Get all addresses associated with the hostname
            addr_info = socket.getaddrinfo(
                hostname,
                None,
                socket.AF_INET,  # IPv4 only for compatibility
                socket.SOCK_STREAM,
            )
            for info in addr_info:
                addr = info[4][0]
                if addr and addr != "127.0.0.1" and addr not in addresses:
                    addresses.append(addr)
        except socket.gaierror:
            pass

        # Fallback: try to get the IP by connecting to an external address
        if not addresses:
            try:
                # This doesn't actually send any packets
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    addr = s.getsockname()[0]
                    if addr and addr != "127.0.0.1":
                        addresses.append(addr)
            except OSError:
                pass

        # Last resort: use loopback
        if not addresses:
            addresses = ["127.0.0.1"]

        return addresses

    def _create_service_info(self) -> ServiceInfo:
        """
        Create the ServiceInfo object for registration.

        Returns:
            ServiceInfo configured with the current settings.
        """
        hostname = socket.gethostname()
        addresses = self._get_local_ip_addresses()

        # Build service name: "{service_name} on {hostname}"
        instance_name = f"{self._config.service_name} on {hostname}"

        # Prepare properties - convert all values to bytes
        properties = {
            "version": "0.1.0",
            "hostname": hostname,
        }
        if self._config.properties:
            properties.update(self._config.properties)

        # Convert string values to bytes for zeroconf
        properties_bytes = {
            k: v.encode("utf-8") if isinstance(v, str) else v
            for k, v in properties.items()
        }

        # Convert addresses to bytes
        parsed_addresses = [socket.inet_aton(addr) for addr in addresses]

        return ServiceInfo(
            type_=self._config.service_type,
            name=f"{instance_name}.{self._config.service_type}",
            port=self._config.port,
            properties=properties_bytes,
            server=f"{hostname}.local.",
            addresses=parsed_addresses,
        )

    def start_advertising(self) -> bool:
        """
        Start advertising the service via mDNS.

        Returns:
            True if advertising started successfully, False otherwise.
        """
        if self._is_advertising:
            logger.warning("mDNS service is already advertising")
            return True

        try:
            # Create Zeroconf instance with IPv4 only for better compatibility
            self._zeroconf = Zeroconf(ip_version=IPVersion.V4Only)

            # Create and register service
            self._service_info = self._create_service_info()
            self._zeroconf.register_service(self._service_info)

            self._is_advertising = True
            logger.info(
                "mDNS service started: %s on port %d",
                self._service_info.name,
                self._config.port,
            )
            logger.debug(
                "Advertising on addresses: %s",
                self._get_local_ip_addresses(),
            )
            return True

        except Exception as e:
            logger.error("Failed to start mDNS advertising: %s", e)
            self._cleanup()
            return False

    def stop_advertising(self) -> None:
        """Stop advertising the service."""
        if not self._is_advertising:
            return

        try:
            if self._zeroconf and self._service_info:
                self._zeroconf.unregister_service(self._service_info)
                logger.info("mDNS service unregistered: %s", self._service_info.name)
        except Exception as e:
            logger.warning("Error unregistering mDNS service: %s", e)
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources."""
        if self._zeroconf:
            try:
                self._zeroconf.close()
            except Exception as e:
                logger.warning("Error closing Zeroconf: %s", e)

        self._zeroconf = None
        self._service_info = None
        self._is_advertising = False

    def update_properties(self, properties: dict[str, str]) -> bool:
        """
        Update the service properties while advertising.

        This will unregister and re-register the service with new properties.

        Args:
            properties: New properties to advertise.

        Returns:
            True if update was successful, False otherwise.
        """
        if not self._is_advertising:
            logger.warning("Cannot update properties: service not advertising")
            return False

        self._config.properties = properties

        # Re-register with new properties
        try:
            if self._zeroconf and self._service_info:
                self._zeroconf.unregister_service(self._service_info)

            self._service_info = self._create_service_info()

            if self._zeroconf:
                self._zeroconf.register_service(self._service_info)
                logger.info("mDNS service properties updated")
                return True
        except Exception as e:
            logger.error("Failed to update mDNS properties: %s", e)

        return False

    def __enter__(self) -> "MDNSService":
        """Context manager entry - starts advertising."""
        self.start_advertising()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - stops advertising."""
        self.stop_advertising()


def create_mdns_service(
    port: int = 8765,
    service_name: str = "Lily Remote Agent",
    properties: Optional[dict[str, str]] = None,
) -> MDNSService:
    """
    Factory function to create an mDNS service with common defaults.

    Args:
        port: Port number the agent is listening on.
        service_name: Human-readable name for the service.
        properties: Additional properties to advertise.

    Returns:
        Configured MDNSService instance (not yet advertising).
    """
    config = ServiceConfig(
        port=port,
        service_name=service_name,
        properties=properties,
    )
    return MDNSService(config)
