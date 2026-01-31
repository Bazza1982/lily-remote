"""Audit logging for all commands and sessions.

Provides a secure audit trail for all operations with timestamps,
client identification, command details, and results.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional


class AuditEventType(Enum):
    """Types of audit events."""
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    COMMAND_SUBMITTED = "command_submitted"
    COMMAND_EXECUTED = "command_executed"
    COMMAND_FAILED = "command_failed"
    PAIRING_REQUEST = "pairing_request"
    PAIRING_APPROVED = "pairing_approved"
    PAIRING_REJECTED = "pairing_rejected"
    PAIRING_REVOKED = "pairing_revoked"
    KILL_SWITCH = "kill_switch"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILURE = "auth_failure"


@dataclass
class AuditEvent:
    """Represents an audit log entry."""
    timestamp: float
    event_type: AuditEventType
    client_id: Optional[str]
    session_id: Optional[str]
    details: dict[str, Any]
    result: Optional[str] = None
    error: Optional[str] = None
    ip_address: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp).isoformat(),
            "event_type": self.event_type.value,
            "client_id": self.client_id,
            "session_id": self.session_id,
            "ip_address": self.ip_address,
            "details": self.details,
            "result": self.result,
            "error": self.error,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"))


class AuditLogger:
    """
    Audit logger for tracking all operations in Lily Remote Agent.

    Features:
    - Logs all commands with timestamp, client_id, command details, and result
    - Rotating log files to manage disk space
    - Thread-safe operation
    - Structured JSON format for easy parsing
    """

    DEFAULT_LOG_DIR = Path("logs")
    DEFAULT_LOG_FILE = "audit.log"
    DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
    DEFAULT_BACKUP_COUNT = 5

    def __init__(
        self,
        log_dir: Optional[Path] = None,
        log_file: str = DEFAULT_LOG_FILE,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ):
        """
        Initialize the audit logger.

        Args:
            log_dir: Directory for log files. Defaults to 'logs' in working dir.
            log_file: Name of the log file.
            max_bytes: Maximum size of each log file before rotation.
            backup_count: Number of backup files to keep.
        """
        self._log_dir = log_dir or self.DEFAULT_LOG_DIR
        self._log_file = log_file
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = threading.Lock()
        self._logger: Optional[logging.Logger] = None
        self._handler: Optional[RotatingFileHandler] = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Ensure the logger is initialized (lazy initialization)."""
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            # Create log directory
            self._log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._log_dir / self._log_file

            # Set up rotating file handler
            self._handler = RotatingFileHandler(
                filename=str(log_path),
                maxBytes=self._max_bytes,
                backupCount=self._backup_count,
                encoding="utf-8",
            )
            self._handler.setLevel(logging.INFO)

            # Use a simple formatter - we'll handle JSON formatting ourselves
            formatter = logging.Formatter("%(message)s")
            self._handler.setFormatter(formatter)

            # Create dedicated logger for audit
            self._logger = logging.getLogger("lily_remote.audit")
            self._logger.setLevel(logging.INFO)
            self._logger.addHandler(self._handler)
            # Don't propagate to root logger
            self._logger.propagate = False

            self._initialized = True

    def log(self, event: AuditEvent) -> None:
        """
        Log an audit event.

        Args:
            event: The audit event to log.
        """
        self._ensure_initialized()

        try:
            self._logger.info(event.to_json())
        except Exception:
            # Don't let audit logging failures affect the application
            pass

    def log_session_start(
        self,
        client_id: str,
        session_id: str,
        ip_address: Optional[str] = None,
    ) -> None:
        """Log session start event."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.SESSION_START,
            client_id=client_id,
            session_id=session_id,
            ip_address=ip_address,
            details={},
            result="started",
        )
        self.log(event)

    def log_session_end(
        self,
        client_id: str,
        session_id: str,
        duration: float,
        commands_executed: int,
        reason: str = "normal",
    ) -> None:
        """Log session end event."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.SESSION_END,
            client_id=client_id,
            session_id=session_id,
            details={
                "duration_seconds": round(duration, 2),
                "commands_executed": commands_executed,
                "reason": reason,
            },
            result="ended",
        )
        self.log(event)

    def log_command_submitted(
        self,
        client_id: str,
        session_id: str,
        command_id: str,
        command_type: str,
        params: dict[str, Any],
        ip_address: Optional[str] = None,
    ) -> None:
        """Log command submission event."""
        # Sanitize params to avoid logging sensitive data
        safe_params = self._sanitize_params(params)

        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.COMMAND_SUBMITTED,
            client_id=client_id,
            session_id=session_id,
            ip_address=ip_address,
            details={
                "command_id": command_id,
                "command_type": command_type,
                "params": safe_params,
            },
            result="queued",
        )
        self.log(event)

    def log_command_executed(
        self,
        client_id: str,
        session_id: str,
        command_id: str,
        command_type: str,
        success: bool,
        execution_time_ms: float,
        result_data: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Log command execution result."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.COMMAND_EXECUTED if success else AuditEventType.COMMAND_FAILED,
            client_id=client_id,
            session_id=session_id,
            details={
                "command_id": command_id,
                "command_type": command_type,
                "execution_time_ms": round(execution_time_ms, 2),
                "result": result_data,
            },
            result="succeeded" if success else "failed",
            error=error,
        )
        self.log(event)

    def log_pairing_request(
        self,
        client_id: str,
        client_name: str,
        ip_address: Optional[str] = None,
    ) -> None:
        """Log pairing request event."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.PAIRING_REQUEST,
            client_id=client_id,
            session_id=None,
            ip_address=ip_address,
            details={
                "client_name": client_name,
            },
            result="pending",
        )
        self.log(event)

    def log_pairing_result(
        self,
        client_id: str,
        client_name: str,
        approved: bool,
        ip_address: Optional[str] = None,
    ) -> None:
        """Log pairing approval/rejection event."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.PAIRING_APPROVED if approved else AuditEventType.PAIRING_REJECTED,
            client_id=client_id,
            session_id=None,
            ip_address=ip_address,
            details={
                "client_name": client_name,
            },
            result="approved" if approved else "rejected",
        )
        self.log(event)

    def log_pairing_revoked(
        self,
        client_id: str,
        reason: str = "user_initiated",
    ) -> None:
        """Log pairing revocation event."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.PAIRING_REVOKED,
            client_id=client_id,
            session_id=None,
            details={
                "reason": reason,
            },
            result="revoked",
        )
        self.log(event)

    def log_kill_switch(
        self,
        sessions_terminated: int,
        triggered_by: str = "user",
        ip_address: Optional[str] = None,
    ) -> None:
        """Log kill switch activation event."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.KILL_SWITCH,
            client_id=None,
            session_id=None,
            ip_address=ip_address,
            details={
                "sessions_terminated": sessions_terminated,
                "triggered_by": triggered_by,
            },
            result="activated",
        )
        self.log(event)

    def log_rate_limited(
        self,
        client_id: Optional[str],
        ip_address: Optional[str],
        endpoint: str,
        limit_type: str = "requests",
    ) -> None:
        """Log rate limiting event."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.RATE_LIMITED,
            client_id=client_id,
            session_id=None,
            ip_address=ip_address,
            details={
                "endpoint": endpoint,
                "limit_type": limit_type,
            },
            result="blocked",
        )
        self.log(event)

    def log_auth_failure(
        self,
        client_id: Optional[str],
        ip_address: Optional[str],
        endpoint: str,
        reason: str,
    ) -> None:
        """Log authentication failure event."""
        event = AuditEvent(
            timestamp=time.time(),
            event_type=AuditEventType.AUTH_FAILURE,
            client_id=client_id,
            session_id=None,
            ip_address=ip_address,
            details={
                "endpoint": endpoint,
                "reason": reason,
            },
            result="denied",
        )
        self.log(event)

    def _sanitize_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Sanitize command parameters to avoid logging sensitive data.

        For type commands, truncates long text. Keeps coordinate and other
        non-sensitive parameters intact.
        """
        safe_params = {}

        for key, value in params.items():
            if key == "text" and isinstance(value, str):
                # Truncate long text and indicate it was truncated
                if len(value) > 100:
                    safe_params[key] = value[:100] + f"... ({len(value)} chars total)"
                else:
                    safe_params[key] = value
            elif key in ("keys",) and isinstance(value, list):
                # Log key sequences (for hotkeys)
                safe_params[key] = value
            else:
                # Keep other params as-is
                safe_params[key] = value

        return safe_params

    def close(self) -> None:
        """Close the audit logger and release resources."""
        with self._lock:
            if self._handler:
                self._handler.close()
                self._handler = None
            if self._logger:
                self._logger.handlers.clear()
                self._logger = None
            self._initialized = False


# Global audit logger instance
_audit_logger: Optional[AuditLogger] = None
_audit_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    """
    Get the global audit logger instance.

    Returns:
        The global AuditLogger instance.
    """
    global _audit_logger

    if _audit_logger is None:
        with _audit_lock:
            if _audit_logger is None:
                _audit_logger = AuditLogger()

    return _audit_logger


def configure_audit_logger(
    log_dir: Optional[Path] = None,
    log_file: str = AuditLogger.DEFAULT_LOG_FILE,
    max_bytes: int = AuditLogger.DEFAULT_MAX_BYTES,
    backup_count: int = AuditLogger.DEFAULT_BACKUP_COUNT,
) -> AuditLogger:
    """
    Configure and return the global audit logger.

    Args:
        log_dir: Directory for log files.
        log_file: Name of the log file.
        max_bytes: Maximum size of each log file before rotation.
        backup_count: Number of backup files to keep.

    Returns:
        The configured AuditLogger instance.
    """
    global _audit_logger

    with _audit_lock:
        # Close existing logger if any
        if _audit_logger:
            _audit_logger.close()

        _audit_logger = AuditLogger(
            log_dir=log_dir,
            log_file=log_file,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )

    return _audit_logger
