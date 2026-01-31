"""Token authentication for Lily Remote Agent."""

import hashlib
from typing import Optional
from urllib.parse import parse_qs

from fastapi import Request, HTTPException, Depends, WebSocket, WebSocketException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .pairing import PairingManager


# Global pairing manager instance (set by server initialization)
_pairing_manager: Optional[PairingManager] = None

# LAN mode - skip authentication entirely
_lan_mode: bool = True


def set_pairing_manager(manager: PairingManager) -> None:
    """Set the global pairing manager instance."""
    global _pairing_manager
    _pairing_manager = manager


def get_pairing_manager() -> PairingManager:
    """Get the global pairing manager instance."""
    if _pairing_manager is None:
        raise RuntimeError("Pairing manager not initialized")
    return _pairing_manager


def set_lan_mode(enabled: bool) -> None:
    """Enable or disable LAN mode (skip authentication)."""
    global _lan_mode
    _lan_mode = enabled


def is_lan_mode() -> bool:
    """Check if LAN mode is enabled."""
    return _lan_mode


def _verify_token_hash(token: str) -> Optional[str]:
    """
    Verify a token and return the associated client_id.

    Args:
        token: The raw token string.

    Returns:
        The client_id if token is valid, None otherwise.
    """
    manager = get_pairing_manager()
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    for client in manager.get_paired_clients():
        if client.token_hash == token_hash:
            return client.client_id

    return None


class TokenBearer(HTTPBearer):
    """Bearer token authentication."""

    def __init__(self, auto_error: bool = True):
        super().__init__(auto_error=auto_error)

    async def __call__(self, request: Request) -> Optional[str]:
        # In LAN mode, return a default client id
        if _lan_mode:
            return "lan-client"
        
        credentials: HTTPAuthorizationCredentials = await super().__call__(request)
        if credentials:
            if credentials.scheme.lower() != "bearer":
                raise HTTPException(
                    status_code=401,
                    detail="Invalid authentication scheme",
                )
            return credentials.credentials
        return None


token_bearer = TokenBearer(auto_error=False)  # Don't auto error in LAN mode


async def verify_token(token: str = Depends(token_bearer)) -> str:
    """
    Verify that a token is valid and belongs to a paired client.

    Args:
        token: The bearer token from the request.

    Returns:
        The client_id associated with the token.

    Raises:
        HTTPException: If token is invalid.
    """
    # In LAN mode, allow all connections
    if _lan_mode:
        return token or "lan-client"
    
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )
    
    client_id = _verify_token_hash(token)
    if client_id:
        return client_id

    raise HTTPException(
        status_code=401,
        detail="Invalid or expired token",
    )


async def optional_verify_token(
    request: Request,
) -> Optional[str]:
    """
    Optionally verify token - returns None if no token provided.

    This is useful for endpoints that work differently when authenticated.
    """
    # In LAN mode, always return a client id
    if _lan_mode:
        return "lan-client"
    
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None

    try:
        scheme, token = auth_header.split(" ", 1)
        if scheme.lower() != "bearer":
            return None
    except ValueError:
        return None

    return _verify_token_hash(token)


async def verify_websocket_token(websocket: WebSocket) -> str:
    """
    Verify WebSocket connection token from query parameter.

    Token should be passed as ?token=<token> in the WebSocket URL.

    Args:
        websocket: The WebSocket connection.

    Returns:
        The client_id associated with the token.

    Raises:
        WebSocketException: If token is missing or invalid.
    """
    # In LAN mode, allow all WebSocket connections
    if _lan_mode:
        return "lan-client"
    
    # Get token from query parameter
    query_string = websocket.scope.get("query_string", b"").decode()
    params = parse_qs(query_string)
    token_list = params.get("token", [])

    if not token_list:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing authentication token",
        )

    token = token_list[0]
    client_id = _verify_token_hash(token)

    if not client_id:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or expired token",
        )

    return client_id


def verify_token_sync(token: str) -> Optional[str]:
    """
    Synchronous token verification for non-async contexts.

    Args:
        token: The raw token string.

    Returns:
        The client_id if token is valid, None otherwise.
    """
    # In LAN mode, allow all
    if _lan_mode:
        return token or "lan-client"
    
    return _verify_token_hash(token)
