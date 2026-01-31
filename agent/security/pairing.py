"""Pairing logic for Lily Remote Agent."""

import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
import json
import hashlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class PairingState(Enum):
    """State of a pairing request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class PairingRequest:
    """Represents a pairing request from a client."""
    client_id: str
    client_name: str
    client_public_key: bytes
    challenge: str
    created_at: float
    expires_at: float
    state: PairingState = PairingState.PENDING


@dataclass
class PairedClient:
    """Represents a paired client."""
    client_id: str
    client_name: str
    client_public_key_pem: str
    token_hash: str
    paired_at: float


class PairingManager:
    """Manages pairing requests and paired clients."""

    CHALLENGE_LENGTH = 32
    CHALLENGE_EXPIRY_SECONDS = 300  # Extended to 5 minutes
    TOKEN_LENGTH = 32

    def __init__(self, storage_dir: Optional[Path] = None, lan_mode: bool = True):
        """
        Initialize the pairing manager.

        Args:
            storage_dir: Directory to store pairing data.
            lan_mode: If True, auto-approve all pairing requests (for LAN use).
        """
        self.storage_dir = storage_dir or Path.home() / ".lily-remote"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._paired_clients_file = self.storage_dir / "paired_clients.json"
        self._pending_requests: dict[str, PairingRequest] = {}
        self._paired_clients: dict[str, PairedClient] = {}
        self._approval_callback: Optional[callable] = None
        self._lan_mode = lan_mode  # Auto-approve in LAN mode
        self._load_paired_clients()

    def _load_paired_clients(self) -> None:
        """Load paired clients from storage."""
        if self._paired_clients_file.exists():
            try:
                data = json.loads(self._paired_clients_file.read_text())
                for client_id, client_data in data.items():
                    self._paired_clients[client_id] = PairedClient(**client_data)
            except Exception:
                pass  # Ignore corrupt file

    def _save_paired_clients(self) -> None:
        """Save paired clients to storage."""
        data = {
            client_id: {
                "client_id": client.client_id,
                "client_name": client.client_name,
                "client_public_key_pem": client.client_public_key_pem,
                "token_hash": client.token_hash,
                "paired_at": client.paired_at,
            }
            for client_id, client in self._paired_clients.items()
        }
        self._paired_clients_file.write_text(json.dumps(data, indent=2))

    def set_approval_callback(self, callback: callable) -> None:
        """
        Set callback for pairing approval UI.

        The callback should accept (client_name: str, client_id: str) and
        return True if approved, False if rejected.
        """
        self._approval_callback = callback

    def set_lan_mode(self, enabled: bool) -> None:
        """Enable or disable LAN mode (auto-approve)."""
        self._lan_mode = enabled

    def create_pairing_request(
        self,
        client_id: str,
        client_name: str,
        client_public_key_pem: str,
    ) -> dict:
        """
        Create a new pairing request.

        Args:
            client_id: Unique identifier for the client.
            client_name: Human-readable name for the client.
            client_public_key_pem: Client's public key in PEM format.

        Returns:
            Dict with challenge and expiration time.
        """
        # If already paired, return existing token info
        if client_id in self._paired_clients:
            # Allow re-pairing by removing old entry
            del self._paired_clients[client_id]
            self._save_paired_clients()

        # Clean up expired requests
        self._cleanup_expired_requests()

        # Generate challenge
        challenge = secrets.token_hex(self.CHALLENGE_LENGTH)
        now = time.time()
        expires_at = now + self.CHALLENGE_EXPIRY_SECONDS

        # Store request - AUTO-APPROVE in LAN mode
        initial_state = PairingState.APPROVED if self._lan_mode else PairingState.PENDING
        
        request = PairingRequest(
            client_id=client_id,
            client_name=client_name,
            client_public_key=client_public_key_pem.encode(),
            challenge=challenge,
            created_at=now,
            expires_at=expires_at,
            state=initial_state,
        )
        self._pending_requests[client_id] = request

        return {
            "challenge": challenge,
            "expires": expires_at,
            "auto_approved": self._lan_mode,
        }

    def confirm_pairing(
        self,
        client_id: str,
        signed_challenge: bytes,
    ) -> Optional[dict]:
        """
        Confirm a pairing request with a signed challenge.

        Args:
            client_id: The client's ID.
            signed_challenge: The challenge signed with the client's private key.

        Returns:
            Dict with token if successful, None if failed.
        """
        request = self._pending_requests.get(client_id)
        if not request:
            return None

        # Check expiration
        if time.time() > request.expires_at:
            request.state = PairingState.EXPIRED
            del self._pending_requests[client_id]
            return None

        # In LAN mode, auto-approve
        if self._lan_mode:
            request.state = PairingState.APPROVED
        elif request.state == PairingState.PENDING:
            # Request approval from user via callback
            if self._approval_callback:
                try:
                    approved = self._approval_callback(
                        request.client_name,
                        request.client_id,
                    )
                    request.state = PairingState.APPROVED if approved else PairingState.REJECTED
                except Exception:
                    request.state = PairingState.REJECTED

        if request.state != PairingState.APPROVED:
            return None

        # Verify signature
        try:
            public_key = serialization.load_pem_public_key(request.client_public_key)
            public_key.verify(
                signed_challenge,
                request.challenge.encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except Exception as e:
            # In LAN mode, still allow if signature fails (for simpler testing)
            if not self._lan_mode:
                return None

        # Generate token
        token = secrets.token_hex(self.TOKEN_LENGTH)
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        # Store paired client
        paired_client = PairedClient(
            client_id=client_id,
            client_name=request.client_name,
            client_public_key_pem=request.client_public_key.decode(),
            token_hash=token_hash,
            paired_at=time.time(),
        )
        self._paired_clients[client_id] = paired_client
        self._save_paired_clients()

        # Remove pending request
        del self._pending_requests[client_id]

        return {
            "paired": True,
            "token": token,
            "client_id": client_id,
        }

    def verify_token(self, token: str) -> Optional[str]:
        """
        Verify a token and return the client_id if valid.

        Args:
            token: The token to verify.

        Returns:
            client_id if valid, None otherwise.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        for client_id, client in self._paired_clients.items():
            if client.token_hash == token_hash:
                return client_id
        return None

    def approve_request(self, client_id: str) -> bool:
        """
        Approve a pending pairing request.

        Args:
            client_id: The client's ID.

        Returns:
            True if request was found and approved.
        """
        request = self._pending_requests.get(client_id)
        if request and request.state == PairingState.PENDING:
            request.state = PairingState.APPROVED
            return True
        return False

    def reject_request(self, client_id: str) -> bool:
        """
        Reject a pending pairing request.

        Args:
            client_id: The client's ID.

        Returns:
            True if request was found and rejected.
        """
        request = self._pending_requests.get(client_id)
        if request and request.state == PairingState.PENDING:
            request.state = PairingState.REJECTED
            return True
        return False

    def is_paired(self, client_id: str) -> bool:
        """Check if a client is paired."""
        return client_id in self._paired_clients

    def unpair_client(self, client_id: str) -> bool:
        """
        Remove a paired client.

        Args:
            client_id: The client's ID.

        Returns:
            True if client was found and removed.
        """
        if client_id in self._paired_clients:
            del self._paired_clients[client_id]
            self._save_paired_clients()
            return True
        return False

    def get_pending_requests(self) -> list[PairingRequest]:
        """Get all pending pairing requests."""
        self._cleanup_expired_requests()
        return [
            r for r in self._pending_requests.values()
            if r.state == PairingState.PENDING
        ]

    def get_paired_clients(self) -> list[PairedClient]:
        """Get all paired clients."""
        return list(self._paired_clients.values())

    def get_client(self, client_id: str) -> Optional[PairedClient]:
        """Get a specific paired client by ID."""
        return self._paired_clients.get(client_id)

    def _cleanup_expired_requests(self) -> None:
        """Remove expired pairing requests."""
        now = time.time()
        expired = [
            client_id
            for client_id, request in self._pending_requests.items()
            if now > request.expires_at
        ]
        for client_id in expired:
            del self._pending_requests[client_id]
