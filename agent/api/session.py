"""Session management for Lily Remote Agent."""

import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SessionState(Enum):
    """State of a control session."""
    ACTIVE = "active"
    ENDED = "ended"


@dataclass
class Session:
    """Represents an active control session."""
    session_id: str
    client_id: str
    started_at: float
    ended_at: Optional[float] = None
    state: SessionState = SessionState.ACTIVE
    command_count: int = 0

    def is_active(self) -> bool:
        """Check if the session is still active."""
        return self.state == SessionState.ACTIVE


class SessionError(Exception):
    """Base exception for session errors."""
    pass


class SessionNotFoundError(SessionError):
    """Raised when a session is not found."""
    pass


class SessionAlreadyActiveError(SessionError):
    """Raised when trying to start a session when one is already active."""
    pass


class SessionNotActiveError(SessionError):
    """Raised when trying to operate on an inactive session."""
    pass


class SessionManager:
    """
    Manages control sessions for the Lily Remote Agent.

    Only one session can be active per client at a time.
    Sessions track command counts and provide session isolation.
    """

    SESSION_ID_LENGTH = 16

    def __init__(self, max_session_duration: float = 3600.0):
        """
        Initialize the session manager.

        Args:
            max_session_duration: Maximum session duration in seconds (default: 1 hour).
        """
        self._sessions: dict[str, Session] = {}
        self._client_sessions: dict[str, str] = {}  # client_id -> session_id
        self._max_session_duration = max_session_duration

    def start_session(self, client_id: str) -> Session:
        """
        Start a new control session for a client.

        Args:
            client_id: The client's unique identifier.

        Returns:
            The newly created Session.

        Raises:
            SessionAlreadyActiveError: If the client already has an active session.
        """
        # Check for existing active session
        existing_session_id = self._client_sessions.get(client_id)
        if existing_session_id:
            existing_session = self._sessions.get(existing_session_id)
            if existing_session and existing_session.is_active():
                # Check if session has expired
                if self._is_session_expired(existing_session):
                    self._end_session_internal(existing_session)
                else:
                    raise SessionAlreadyActiveError(
                        f"Client {client_id} already has an active session: {existing_session_id}"
                    )

        # Generate session ID
        session_id = secrets.token_hex(self.SESSION_ID_LENGTH)

        # Create session
        session = Session(
            session_id=session_id,
            client_id=client_id,
            started_at=time.time(),
        )

        self._sessions[session_id] = session
        self._client_sessions[client_id] = session_id

        return session

    def end_session(self, session_id: str, client_id: str) -> Session:
        """
        End a control session.

        Args:
            session_id: The session's unique identifier.
            client_id: The client's unique identifier (for authorization).

        Returns:
            The ended Session.

        Raises:
            SessionNotFoundError: If the session is not found.
            SessionNotActiveError: If the session is not active.
            SessionError: If the client_id doesn't match the session owner.
        """
        session = self._sessions.get(session_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.client_id != client_id:
            raise SessionError(f"Session {session_id} does not belong to client {client_id}")

        if not session.is_active():
            raise SessionNotActiveError(f"Session {session_id} is not active")

        self._end_session_internal(session)
        return session

    def _end_session_internal(self, session: Session) -> None:
        """Internal method to end a session."""
        session.ended_at = time.time()
        session.state = SessionState.ENDED

        # Remove from client mapping
        if self._client_sessions.get(session.client_id) == session.session_id:
            del self._client_sessions[session.client_id]

    def get_session(self, session_id: str) -> Optional[Session]:
        """
        Get a session by ID.

        Args:
            session_id: The session's unique identifier.

        Returns:
            The Session if found, None otherwise.
        """
        session = self._sessions.get(session_id)
        if session and self._is_session_expired(session) and session.is_active():
            self._end_session_internal(session)
        return session

    def get_active_session(self, client_id: str) -> Optional[Session]:
        """
        Get the active session for a client.

        Args:
            client_id: The client's unique identifier.

        Returns:
            The active Session if one exists, None otherwise.
        """
        session_id = self._client_sessions.get(client_id)
        if not session_id:
            return None

        session = self._sessions.get(session_id)
        if not session or not session.is_active():
            return None

        if self._is_session_expired(session):
            self._end_session_internal(session)
            return None

        return session

    def validate_session(self, session_id: str, client_id: str) -> Session:
        """
        Validate that a session exists, is active, and belongs to the client.

        Args:
            session_id: The session's unique identifier.
            client_id: The client's unique identifier.

        Returns:
            The validated Session.

        Raises:
            SessionNotFoundError: If the session is not found.
            SessionNotActiveError: If the session is not active or has expired.
            SessionError: If the session doesn't belong to the client.
        """
        session = self._sessions.get(session_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        if session.client_id != client_id:
            raise SessionError(f"Session {session_id} does not belong to client {client_id}")

        if not session.is_active():
            raise SessionNotActiveError(f"Session {session_id} is not active")

        if self._is_session_expired(session):
            self._end_session_internal(session)
            raise SessionNotActiveError(f"Session {session_id} has expired")

        return session

    def increment_command_count(self, session_id: str) -> int:
        """
        Increment the command count for a session.

        Args:
            session_id: The session's unique identifier.

        Returns:
            The new command count.

        Raises:
            SessionNotFoundError: If the session is not found.
        """
        session = self._sessions.get(session_id)
        if not session:
            raise SessionNotFoundError(f"Session {session_id} not found")

        session.command_count += 1
        return session.command_count

    def get_active_sessions(self) -> list[Session]:
        """
        Get all active sessions.

        Returns:
            List of active Session objects.
        """
        # Clean up expired sessions
        for session in list(self._sessions.values()):
            if session.is_active() and self._is_session_expired(session):
                self._end_session_internal(session)

        return [s for s in self._sessions.values() if s.is_active()]

    def _is_session_expired(self, session: Session) -> bool:
        """Check if a session has exceeded the maximum duration."""
        return time.time() - session.started_at > self._max_session_duration

    def force_end_all_sessions(self) -> int:
        """
        Force end all active sessions (for kill switch).

        Returns:
            Number of sessions ended.
        """
        count = 0
        for session in list(self._sessions.values()):
            if session.is_active():
                self._end_session_internal(session)
                count += 1
        return count
