"""Client-side pairing with remote agents.

Handles the pairing handshake, RSA key generation, challenge signing,
and credential storage for connecting to Lily Remote agents.
"""

import base64
import json
import logging
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .discovery import DiscoveredAgent

logger = logging.getLogger(__name__)


class PairingError(Exception):
    """Base exception for pairing errors."""

    pass


class PairingRequestError(PairingError):
    """Error during pairing request phase."""

    pass


class PairingConfirmError(PairingError):
    """Error during pairing confirmation phase."""

    pass


class PairingRejectedError(PairingError):
    """The agent user rejected the pairing request."""

    pass


class PairingExpiredError(PairingError):
    """The pairing challenge expired before confirmation."""

    pass


class KeyGenerationError(PairingError):
    """Error generating RSA keys."""

    pass


@dataclass
class PairedAgent:
    """Represents a successfully paired agent with stored credentials."""

    client_id: str
    client_name: str
    agent_hostname: str
    agent_address: str
    agent_port: int
    token: str
    paired_at: float
    private_key_pem: str
    public_key_pem: str

    @property
    def base_url(self) -> str:
        """Get the base URL for the agent."""
        return f"https://{self.agent_address}:{self.agent_port}"

    @property
    def websocket_url(self) -> str:
        """Get the WebSocket URL for the events endpoint."""
        return f"wss://{self.agent_address}:{self.agent_port}/events"


class ClientKeyPair:
    """RSA key pair for client authentication."""

    RSA_KEY_SIZE = 2048
    RSA_PUBLIC_EXPONENT = 65537

    def __init__(
        self,
        private_key: Optional[rsa.RSAPrivateKey] = None,
        public_key: Optional[rsa.RSAPublicKey] = None,
    ):
        """
        Initialize the key pair.

        Args:
            private_key: Existing private key, or None to generate new.
            public_key: Existing public key, derived from private if not provided.
        """
        if private_key is None:
            self._private_key, self._public_key = self._generate_key_pair()
        else:
            self._private_key = private_key
            self._public_key = public_key or private_key.public_key()

    def _generate_key_pair(self) -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
        """Generate a new RSA key pair."""
        try:
            private_key = rsa.generate_private_key(
                public_exponent=self.RSA_PUBLIC_EXPONENT,
                key_size=self.RSA_KEY_SIZE,
            )
            public_key = private_key.public_key()
            return private_key, public_key
        except Exception as e:
            raise KeyGenerationError(f"Failed to generate RSA key pair: {e}") from e

    @property
    def private_key(self) -> rsa.RSAPrivateKey:
        """Get the private key."""
        return self._private_key

    @property
    def public_key(self) -> rsa.RSAPublicKey:
        """Get the public key."""
        return self._public_key

    @property
    def private_key_pem(self) -> str:
        """Get the private key in PEM format."""
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

    @property
    def public_key_pem(self) -> str:
        """Get the public key in PEM format."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def sign_challenge(self, challenge: str) -> bytes:
        """
        Sign a challenge string with the private key.

        Args:
            challenge: The challenge string to sign.

        Returns:
            The signature as bytes.
        """
        return self._private_key.sign(
            challenge.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    @classmethod
    def from_pem(cls, private_key_pem: str) -> "ClientKeyPair":
        """
        Load a key pair from a PEM-encoded private key.

        Args:
            private_key_pem: PEM-encoded private key string.

        Returns:
            ClientKeyPair instance.
        """
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"),
            password=None,
        )
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise KeyGenerationError("Loaded key is not an RSA private key")
        return cls(private_key=private_key)


class CredentialStore:
    """Persistent storage for paired agent credentials."""

    def __init__(self, storage_dir: Optional[Path] = None):
        """
        Initialize the credential store.

        Args:
            storage_dir: Directory to store credentials. Defaults to ~/.lily-remote-client
        """
        self._storage_dir = storage_dir or Path.home() / ".lily-remote-client"
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._credentials_file = self._storage_dir / "paired_agents.json"
        self._paired_agents: dict[str, PairedAgent] = {}
        self._load()

    def _load(self) -> None:
        """Load credentials from storage."""
        if not self._credentials_file.exists():
            return

        try:
            data = json.loads(self._credentials_file.read_text(encoding="utf-8"))
            for agent_key, agent_data in data.items():
                self._paired_agents[agent_key] = PairedAgent(**agent_data)
            logger.debug("Loaded %d paired agents", len(self._paired_agents))
        except Exception as e:
            logger.warning("Failed to load credentials: %s", e)

    def _save(self) -> None:
        """Save credentials to storage."""
        data = {}
        for agent_key, agent in self._paired_agents.items():
            data[agent_key] = {
                "client_id": agent.client_id,
                "client_name": agent.client_name,
                "agent_hostname": agent.agent_hostname,
                "agent_address": agent.agent_address,
                "agent_port": agent.agent_port,
                "token": agent.token,
                "paired_at": agent.paired_at,
                "private_key_pem": agent.private_key_pem,
                "public_key_pem": agent.public_key_pem,
            }

        self._credentials_file.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8",
        )

        # Set restrictive permissions on credentials file (owner read/write only)
        try:
            self._credentials_file.chmod(0o600)
        except OSError:
            pass  # Windows may not support this

    def _make_agent_key(self, hostname: str, address: str, port: int) -> str:
        """Create a unique key for an agent."""
        return f"{hostname}:{address}:{port}"

    def get(
        self,
        hostname: str,
        address: str,
        port: int,
    ) -> Optional[PairedAgent]:
        """Get credentials for a specific agent."""
        key = self._make_agent_key(hostname, address, port)
        return self._paired_agents.get(key)

    def get_by_address(self, address: str, port: int) -> Optional[PairedAgent]:
        """Get credentials for an agent by address and port."""
        for agent in self._paired_agents.values():
            if agent.agent_address == address and agent.agent_port == port:
                return agent
        return None

    def store(self, agent: PairedAgent) -> None:
        """Store credentials for a paired agent."""
        key = self._make_agent_key(
            agent.agent_hostname,
            agent.agent_address,
            agent.agent_port,
        )
        self._paired_agents[key] = agent
        self._save()
        logger.info("Stored credentials for agent: %s", agent.agent_hostname)

    def remove(self, hostname: str, address: str, port: int) -> bool:
        """Remove credentials for an agent."""
        key = self._make_agent_key(hostname, address, port)
        if key in self._paired_agents:
            del self._paired_agents[key]
            self._save()
            logger.info("Removed credentials for agent: %s", hostname)
            return True
        return False

    def list_paired_agents(self) -> list[PairedAgent]:
        """Get all paired agents."""
        return list(self._paired_agents.values())

    def clear(self) -> None:
        """Remove all stored credentials."""
        self._paired_agents.clear()
        self._save()
        logger.info("Cleared all stored credentials")


class PairingClient:
    """
    Client for pairing with Lily Remote agents.

    Handles the complete pairing flow:
    1. Generate RSA key pair
    2. Request pairing (sends public key, receives challenge)
    3. Sign challenge with private key
    4. Confirm pairing (sends signed challenge, receives token)
    5. Store credentials for future use
    """

    DEFAULT_TIMEOUT = 10.0

    def __init__(
        self,
        client_id: str,
        client_name: str,
        credential_store: Optional[CredentialStore] = None,
    ):
        """
        Initialize the pairing client.

        Args:
            client_id: Unique identifier for this client.
            client_name: Human-readable name for this client.
            credential_store: Optional credential store. Creates new one if not provided.
        """
        self._client_id = client_id
        self._client_name = client_name
        self._credential_store = credential_store or CredentialStore()

    @property
    def client_id(self) -> str:
        """Get the client ID."""
        return self._client_id

    @property
    def client_name(self) -> str:
        """Get the client name."""
        return self._client_name

    def _create_http_client(self) -> httpx.Client:
        """Create an HTTP client that accepts self-signed certificates."""
        # Create SSL context that doesn't verify certificates
        # (Required for initial pairing with self-signed certs)
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        return httpx.Client(
            timeout=self.DEFAULT_TIMEOUT,
            verify=ssl_context,
        )

    def get_stored_credentials(
        self,
        agent: DiscoveredAgent,
    ) -> Optional[PairedAgent]:
        """
        Get stored credentials for a discovered agent.

        Args:
            agent: The discovered agent.

        Returns:
            Stored credentials if available, None otherwise.
        """
        return self._credential_store.get(
            hostname=agent.hostname,
            address=agent.primary_address,
            port=agent.port,
        )

    def is_paired(self, agent: DiscoveredAgent) -> bool:
        """Check if we have stored credentials for an agent."""
        return self.get_stored_credentials(agent) is not None

    def pair(
        self,
        agent: DiscoveredAgent,
        wait_for_approval: bool = True,
        approval_timeout: float = 60.0,
        poll_interval: float = 1.0,
    ) -> PairedAgent:
        """
        Pair with a discovered agent.

        This method performs the complete pairing flow:
        1. Generates a new RSA key pair
        2. Requests pairing from the agent
        3. Waits for user approval on the agent side
        4. Confirms pairing with signed challenge
        5. Stores and returns the paired agent credentials

        Args:
            agent: The discovered agent to pair with.
            wait_for_approval: Whether to poll for approval or return immediately.
            approval_timeout: How long to wait for user approval (seconds).
            poll_interval: How often to poll for approval (seconds).

        Returns:
            PairedAgent with credentials.

        Raises:
            PairingRequestError: If the pairing request fails.
            PairingConfirmError: If the pairing confirmation fails.
            PairingRejectedError: If the user rejects the pairing.
            PairingExpiredError: If the challenge expires.
        """
        base_url = agent.url
        if not base_url:
            raise PairingRequestError("Agent has no valid address")

        # Generate new key pair for this pairing
        key_pair = ClientKeyPair()
        logger.debug("Generated new RSA key pair for pairing")

        # Step 1: Request pairing
        challenge, expires = self._request_pairing(base_url, key_pair)
        logger.info(
            "Pairing requested with %s - waiting for user approval",
            agent.hostname,
        )

        # Step 2: Wait for approval and confirm
        if wait_for_approval:
            token = self._confirm_pairing_with_retry(
                base_url=base_url,
                key_pair=key_pair,
                challenge=challenge,
                expires=expires,
                timeout=approval_timeout,
                poll_interval=poll_interval,
            )
        else:
            # Single attempt - user must have already approved
            token = self._confirm_pairing(base_url, key_pair, challenge)

        # Step 3: Create and store paired agent
        paired_agent = PairedAgent(
            client_id=self._client_id,
            client_name=self._client_name,
            agent_hostname=agent.hostname,
            agent_address=agent.primary_address,
            agent_port=agent.port,
            token=token,
            paired_at=time.time(),
            private_key_pem=key_pair.private_key_pem,
            public_key_pem=key_pair.public_key_pem,
        )

        self._credential_store.store(paired_agent)
        logger.info("Successfully paired with agent: %s", agent.hostname)

        return paired_agent

    def _request_pairing(
        self,
        base_url: str,
        key_pair: ClientKeyPair,
    ) -> tuple[str, float]:
        """
        Request pairing from the agent.

        Returns:
            Tuple of (challenge, expires_timestamp).
        """
        url = urljoin(base_url, "/pair/request")

        try:
            with self._create_http_client() as client:
                response = client.post(
                    url,
                    json={
                        "client_id": self._client_id,
                        "client_name": self._client_name,
                        "public_key": key_pair.public_key_pem,
                    },
                )

            if response.status_code == 400:
                error = response.json().get("detail", "Bad request")
                raise PairingRequestError(f"Pairing request rejected: {error}")

            if response.status_code != 200:
                raise PairingRequestError(
                    f"Pairing request failed: HTTP {response.status_code}"
                )

            data = response.json()
            challenge = data["challenge"]
            expires = data["expires"]

            return challenge, expires

        except httpx.RequestError as e:
            raise PairingRequestError(f"Failed to connect to agent: {e}") from e

    def _confirm_pairing(
        self,
        base_url: str,
        key_pair: ClientKeyPair,
        challenge: str,
    ) -> str:
        """
        Confirm pairing with signed challenge.

        Returns:
            The authentication token.
        """
        url = urljoin(base_url, "/pair/confirm")

        # Sign the challenge
        signature = key_pair.sign_challenge(challenge)
        signed_challenge_b64 = base64.b64encode(signature).decode("utf-8")

        try:
            with self._create_http_client() as client:
                response = client.post(
                    url,
                    json={
                        "client_id": self._client_id,
                        "signed_challenge": signed_challenge_b64,
                    },
                )

            if response.status_code == 401:
                # Could be pending, rejected, expired, or invalid signature
                detail = response.json().get("detail", "")
                if "expired" in detail.lower():
                    raise PairingExpiredError("Pairing challenge expired")
                if "rejected" in detail.lower():
                    raise PairingRejectedError("Pairing was rejected by user")
                # Still pending - return None to indicate retry needed
                return ""

            if response.status_code != 200:
                raise PairingConfirmError(
                    f"Pairing confirmation failed: HTTP {response.status_code}"
                )

            data = response.json()
            if not data.get("paired"):
                raise PairingConfirmError("Pairing confirmation returned paired=false")

            return data["token"]

        except httpx.RequestError as e:
            raise PairingConfirmError(f"Failed to connect to agent: {e}") from e

    def _confirm_pairing_with_retry(
        self,
        base_url: str,
        key_pair: ClientKeyPair,
        challenge: str,
        expires: float,
        timeout: float,
        poll_interval: float,
    ) -> str:
        """
        Confirm pairing with retries while waiting for user approval.

        Returns:
            The authentication token.
        """
        start_time = time.time()
        deadline = min(start_time + timeout, expires)

        while time.time() < deadline:
            try:
                token = self._confirm_pairing(base_url, key_pair, challenge)
                if token:
                    return token
                # Empty token means still pending - wait and retry
            except PairingExpiredError:
                raise
            except PairingRejectedError:
                raise
            except PairingConfirmError as e:
                logger.debug("Pairing confirmation attempt failed: %s", e)

            # Wait before retrying
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(poll_interval, remaining))

        # Check if we expired
        if time.time() >= expires:
            raise PairingExpiredError("Pairing challenge expired before approval")
        else:
            raise PairingConfirmError(
                "Pairing approval timeout - user did not approve in time"
            )

    def unpair(self, agent: DiscoveredAgent) -> bool:
        """
        Remove stored credentials for an agent.

        Args:
            agent: The agent to unpair from.

        Returns:
            True if credentials were removed, False if not found.
        """
        return self._credential_store.remove(
            hostname=agent.hostname,
            address=agent.primary_address,
            port=agent.port,
        )

    def list_paired_agents(self) -> list[PairedAgent]:
        """Get all paired agents."""
        return self._credential_store.list_paired_agents()


def generate_client_id() -> str:
    """
    Generate a unique client ID.

    Returns:
        A unique client ID string.
    """
    import secrets
    import socket

    hostname = socket.gethostname()
    random_suffix = secrets.token_hex(4)
    return f"{hostname}-{random_suffix}"


def create_pairing_client(
    client_name: str = "Lily Remote Client",
    client_id: Optional[str] = None,
    storage_dir: Optional[Path] = None,
) -> PairingClient:
    """
    Factory function to create a pairing client.

    Args:
        client_name: Human-readable name for the client.
        client_id: Unique client ID (generated if not provided).
        storage_dir: Directory for credential storage.

    Returns:
        Configured PairingClient instance.
    """
    if client_id is None:
        client_id = generate_client_id()

    credential_store = CredentialStore(storage_dir)

    return PairingClient(
        client_id=client_id,
        client_name=client_name,
        credential_store=credential_store,
    )
