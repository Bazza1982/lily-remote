"""FastAPI server for Lily Remote Agent."""

import asyncio
import base64
import collections
import json
import logging
import platform
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from ..security.pairing import PairingManager
from ..security.auth import set_pairing_manager, verify_token, verify_websocket_token
from ..control.screen import FrameStreamer, ScreenCapture, FrameMetrics, get_primary_monitor_info
from ..audit.logger import get_audit_logger, AuditLogger
from .session import (
    SessionManager,
    SessionError,
    SessionNotFoundError,
    SessionAlreadyActiveError,
    SessionNotActiveError,
)
from .commands import (
    CommandQueue,
    CommandError,
    CommandNotFoundError,
    InvalidCommandError,
    create_command_queue,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Rate Limiter
# =============================================================================

@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""
    # Global rate limits
    requests_per_minute: int = 120  # Max requests per minute per IP
    requests_per_second: int = 10   # Max requests per second per IP

    # Endpoint-specific limits
    pairing_per_minute: int = 5     # Max pairing attempts per minute per IP
    commands_per_second: int = 20   # Max command batches per second per session
    websocket_messages_per_second: int = 30  # Max WS messages per second

    # Burst allowance
    burst_multiplier: float = 1.5   # Allow short bursts up to this multiplier


class RateLimiter:
    """
    Token bucket rate limiter for API endpoints.

    Implements per-IP and per-endpoint rate limiting with configurable
    limits and burst allowance.
    """

    def __init__(self, config: Optional[RateLimitConfig] = None):
        """
        Initialize the rate limiter.

        Args:
            config: Rate limiting configuration.
        """
        self._config = config or RateLimitConfig()
        self._buckets: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._cleanup_interval = 60.0  # Clean up old buckets every 60 seconds
        self._last_cleanup = time.time()

    async def check_rate_limit(
        self,
        key: str,
        limit_per_second: float,
        burst_size: Optional[int] = None,
    ) -> tuple[bool, float]:
        """
        Check if a request is within rate limits.

        Args:
            key: Unique identifier for the rate limit bucket (e.g., IP address).
            limit_per_second: Maximum requests per second.
            burst_size: Maximum burst size (defaults to limit * burst_multiplier).

        Returns:
            Tuple of (allowed, retry_after_seconds).
        """
        if burst_size is None:
            burst_size = int(limit_per_second * self._config.burst_multiplier)

        async with self._lock:
            now = time.time()

            # Periodic cleanup
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup_old_buckets(now)
                self._last_cleanup = now

            # Get or create bucket
            if key not in self._buckets:
                self._buckets[key] = {
                    "tokens": float(burst_size),
                    "last_update": now,
                }

            bucket = self._buckets[key]

            # Refill tokens based on elapsed time
            elapsed = now - bucket["last_update"]
            bucket["tokens"] = min(
                burst_size,
                bucket["tokens"] + elapsed * limit_per_second,
            )
            bucket["last_update"] = now

            # Check if we have a token available
            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
                return (True, 0.0)
            else:
                # Calculate retry-after
                tokens_needed = 1.0 - bucket["tokens"]
                retry_after = tokens_needed / limit_per_second
                return (False, retry_after)

    def _cleanup_old_buckets(self, now: float) -> None:
        """Remove buckets that haven't been used recently."""
        max_age = 300.0  # 5 minutes
        to_remove = [
            key for key, bucket in self._buckets.items()
            if now - bucket["last_update"] > max_age
        ]
        for key in to_remove:
            del self._buckets[key]

    async def check_global_limit(self, ip: str) -> tuple[bool, float]:
        """Check global rate limit for an IP."""
        return await self.check_rate_limit(
            f"global:{ip}",
            self._config.requests_per_second,
        )

    async def check_pairing_limit(self, ip: str) -> tuple[bool, float]:
        """Check pairing rate limit for an IP."""
        return await self.check_rate_limit(
            f"pairing:{ip}",
            self._config.pairing_per_minute / 60.0,
            burst_size=2,
        )

    async def check_command_limit(self, session_id: str) -> tuple[bool, float]:
        """Check command submission rate limit for a session."""
        return await self.check_rate_limit(
            f"commands:{session_id}",
            self._config.commands_per_second,
        )

    async def check_websocket_limit(self, client_id: str) -> tuple[bool, float]:
        """Check WebSocket message rate limit for a client."""
        return await self.check_rate_limit(
            f"ws:{client_id}",
            self._config.websocket_messages_per_second,
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for applying global rate limiting."""

    def __init__(self, app, rate_limiter: RateLimiter, audit_logger: AuditLogger):
        super().__init__(app)
        self._rate_limiter = rate_limiter
        self._audit_logger = audit_logger

    async def dispatch(self, request: Request, call_next):
        # Get client IP
        client_ip = self._get_client_ip(request)

        # Skip rate limiting for health check
        if request.url.path == "/health":
            return await call_next(request)

        # Check global rate limit
        allowed, retry_after = await self._rate_limiter.check_global_limit(client_ip)

        if not allowed:
            # Log rate limiting event
            self._audit_logger.log_rate_limited(
                client_id=None,
                ip_address=client_ip,
                endpoint=request.url.path,
                limit_type="global",
            )

            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests",
                    "retry_after": round(retry_after, 2),
                },
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """Get the client IP address from the request."""
        # Check for forwarded header (if behind a proxy)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"


# =============================================================================
# Kill Switch State
# =============================================================================

@dataclass
class KillSwitchState:
    """State for the kill switch functionality."""
    active: bool = False
    activated_at: Optional[float] = None
    activated_by: Optional[str] = None
    reason: Optional[str] = None


class KillSwitchResponse(BaseModel):
    """Response for kill switch operations."""
    activated: bool
    sessions_terminated: int
    message: str


class PairRequestBody(BaseModel):
    """Request body for pairing request."""
    client_id: str
    client_name: str
    public_key: str  # PEM-encoded RSA public key


class PairConfirmBody(BaseModel):
    """Request body for pairing confirmation."""
    client_id: str
    signed_challenge: str  # Base64-encoded signature


class SessionEndBody(BaseModel):
    """Request body for ending a session."""
    session_id: str


class SubmitCommandsBody(BaseModel):
    """Request body for submitting commands."""
    session_id: str
    commands: list[dict[str, Any]] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Response for health check."""
    status: str
    version: str
    hostname: str
    platform: str
    uptime: float


class ScreenInfoResponse(BaseModel):
    """Response for screen info."""
    width: int
    height: int
    dpi: int


class SessionResponse(BaseModel):
    """Response for session operations."""
    session_id: str
    client_id: str
    started_at: float
    command_count: int = 0


class SessionEndResponse(BaseModel):
    """Response for session end."""
    ended: bool
    session_id: str
    duration: float
    commands_executed: int


class CommandsResponse(BaseModel):
    """Response for command submission."""
    queued: list[str]


class CommandStatusResponse(BaseModel):
    """Response for command status."""
    id: str
    type: str
    status: str
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class ChatMessageBody(BaseModel):
    """Request body for sending a chat message."""
    from_agent: str  # Sender agent name (e.g., "xiaoxia")
    message: str  # Message content
    auth_token: Optional[str] = None  # Optional auth token for security


class ChatMessage(BaseModel):
    """A chat message."""
    timestamp: str
    from_agent: str
    to_agent: str
    message: str
    direction: str  # "incoming" or "outgoing"


class ChatResponse(BaseModel):
    """Response for chat operations."""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class ChatHistoryResponse(BaseModel):
    """Response for chat history."""
    messages: list[ChatMessage]
    count: int


@dataclass
class FrameStreamConfig:
    """Configuration for frame streaming."""
    min_fps: float = 2.0
    max_fps: float = 10.0
    initial_fps: float = 5.0
    min_quality: int = 30
    max_quality: int = 90
    initial_quality: int = 70
    adaptive_quality: bool = True
    scale: float = 1.0
    monitor_index: int = 0


# Global state
_start_time: float = 0
_pairing_manager: Optional[PairingManager] = None
_session_manager: Optional[SessionManager] = None
_command_queue: Optional[CommandQueue] = None
_frame_streamer: Optional[FrameStreamer] = None
_frame_config: Optional[FrameStreamConfig] = None
_connected_websockets: set[WebSocket] = set()
_streaming_clients: set[WebSocket] = set()  # Clients that want frame streaming
_rate_limiter: Optional[RateLimiter] = None
_rate_limit_config: Optional[RateLimitConfig] = None
_kill_switch_state: KillSwitchState = KillSwitchState()
_audit_logger: Optional[AuditLogger] = None
_on_session_change_callback: Optional[Callable[[int, Optional[str]], None]] = None


def _get_client_ip(request: Request) -> str:
    """Get the client IP address from a request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def broadcast_event(event_type: str, event_data: dict[str, Any]) -> None:
    """Broadcast an event to all connected WebSocket clients."""
    message = json.dumps({"type": event_type, **event_data})
    disconnected = []

    for ws in _connected_websockets:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        _connected_websockets.discard(ws)
        _streaming_clients.discard(ws)


async def broadcast_frame(b64_data: str, metrics: FrameMetrics) -> None:
    """Broadcast a frame to all streaming clients."""
    if not _streaming_clients:
        return

    message = json.dumps({
        "type": "frame",
        "data": b64_data,
        "timestamp": metrics.timestamp,
        "quality": metrics.quality,
        "size_bytes": metrics.frame_size_bytes,
    })

    disconnected = []
    for ws in _streaming_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.append(ws)

    for ws in disconnected:
        _streaming_clients.discard(ws)
        _connected_websockets.discard(ws)


def create_app(
    pairing_manager: PairingManager,
    session_manager: Optional[SessionManager] = None,
    command_queue: Optional[CommandQueue] = None,
    frame_config: Optional[FrameStreamConfig] = None,
    rate_limit_config: Optional[RateLimitConfig] = None,
    on_session_change: Optional[Callable[[int, Optional[str]], None]] = None,
) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        pairing_manager: The pairing manager instance.
        session_manager: Optional session manager (creates new one if not provided).
        command_queue: Optional command queue (creates new one if not provided).
        frame_config: Optional frame streaming configuration.
        rate_limit_config: Optional rate limiting configuration.
        on_session_change: Callback when session count changes (count, client_name).

    Returns:
        Configured FastAPI application.
    """
    global _pairing_manager, _session_manager, _command_queue, _start_time
    global _frame_streamer, _frame_config, _rate_limiter, _rate_limit_config
    global _audit_logger, _on_session_change_callback, _kill_switch_state

    _pairing_manager = pairing_manager
    _session_manager = session_manager or SessionManager()
    _command_queue = command_queue or create_command_queue()
    _frame_config = frame_config or FrameStreamConfig()
    _rate_limit_config = rate_limit_config or RateLimitConfig()
    _rate_limiter = RateLimiter(_rate_limit_config)
    _audit_logger = get_audit_logger()
    _on_session_change_callback = on_session_change
    _kill_switch_state = KillSwitchState()
    _start_time = time.time()

    # Set up auth module's reference to pairing manager
    set_pairing_manager(pairing_manager)

    # Set up command queue event callback
    _command_queue.set_event_callback(broadcast_event)

    # Set up frame streamer
    capture = ScreenCapture(
        monitor_index=_frame_config.monitor_index,
        default_quality=_frame_config.initial_quality,
    )
    _frame_streamer = FrameStreamer(
        capture=capture,
        min_fps=_frame_config.min_fps,
        max_fps=_frame_config.max_fps,
        initial_fps=_frame_config.initial_fps,
        adaptive_quality=_frame_config.adaptive_quality,
        min_quality=_frame_config.min_quality,
        max_quality=_frame_config.max_quality,
        initial_quality=_frame_config.initial_quality,
        scale=_frame_config.scale,
    )
    _frame_streamer.set_frame_callback(broadcast_frame)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start command processing
        await _command_queue.start_processing()
        yield
        # Stop frame streaming
        if _frame_streamer:
            await _frame_streamer.stop()
            _frame_streamer.close()
        # Stop command processing
        await _command_queue.stop_processing()
        # End all sessions
        _session_manager.force_end_all_sessions()
        # Close audit logger
        if _audit_logger:
            _audit_logger.close()

    app = FastAPI(
        title="Lily Remote Agent",
        description="Remote PC control agent for AI systems",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Add rate limiting middleware
    app.add_middleware(RateLimitMiddleware, rate_limiter=_rate_limiter, audit_logger=_audit_logger)

    # === Health endpoints (no auth required) ===

    @app.get("/health", response_model=HealthResponse)
    async def health_check():
        """Health check endpoint - no authentication required."""
        return HealthResponse(
            status="healthy",
            version="1.0.0",
            hostname=socket.gethostname(),
            platform=platform.system(),
            uptime=time.time() - _start_time,
        )

    # === Chat endpoints (for agent-to-agent communication) ===
    
    # Chat storage path
    import os
    from pathlib import Path
    from datetime import datetime
    
    def _get_chat_file() -> Path:
        """Get the path to the chat history file."""
        if platform.system() == "Windows":
            base = Path(os.environ.get("USERPROFILE", "")) / ".lily-remote"
        else:
            base = Path.home() / ".lily-remote"
        base.mkdir(parents=True, exist_ok=True)
        return base / "chat_history.json"
    
    def _load_chat_history() -> list[dict]:
        """Load chat history from file."""
        chat_file = _get_chat_file()
        if chat_file.exists():
            try:
                return json.loads(chat_file.read_text(encoding="utf-8"))
            except:
                return []
        return []
    
    def _save_chat_message(msg: dict):
        """Append a message to chat history."""
        history = _load_chat_history()
        history.append(msg)
        # Keep last 1000 messages
        if len(history) > 1000:
            history = history[-1000:]
        _get_chat_file().write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_auth_token() -> Optional[str]:
        """Get the configured auth token for chat."""
        # Check common locations for auth token
        token_paths = []
        if platform.system() == "Windows":
            token_paths = [
                Path(os.environ.get("USERPROFILE", "")) / "clawd" / "memory" / "secrets" / "help-auth-code.txt",
                Path(os.environ.get("USERPROFILE", "")) / ".lily-remote" / "chat-token.txt",
            ]
        else:
            token_paths = [
                Path.home() / "clawd" / "memory" / "secrets" / "help-auth-code.txt",
                Path.home() / ".lily-remote" / "chat-token.txt",
            ]
        
        for path in token_paths:
            if path.exists():
                try:
                    return path.read_text(encoding="utf-8").strip()
                except:
                    pass
        return None
    
    def _verify_chat_token(provided_token: Optional[str]) -> bool:
        """Verify the chat auth token. Returns True if valid or no token configured."""
        stored_token = _get_auth_token()
        if not stored_token:
            # No token configured = allow all (LAN mode)
            return True
        if not provided_token:
            return False
        return provided_token == stored_token

    def _trigger_local_agent(from_agent: str, message: str):
        """Trigger local Clawdbot agent to respond to chat message.
        
        Uses `clawdbot cron wake` CLI command instead of HTTP API,
        because Gateway wake is implemented via WebSocket RPC, not REST.
        """
        import threading
        import subprocess
        import shlex
        
        def do_trigger():
            try:
                # Construct wake message
                wake_text = f"收到来自 {from_agent} 的聊天消息，请检查 curl -k https://127.0.0.1:8765/chat/history 并回复！"
                
                # Use clawdbot CLI to trigger wake (works on both Windows and Linux)
                # clawdbot system event --mode now triggers immediate heartbeat
                cmd = ["clawdbot", "system", "event", "--text", wake_text, "--mode", "now"]
                
                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=10,
                        shell=(platform.system() == "Windows")  # Windows needs shell=True for PATH
                    )
                    
                    if result.returncode == 0:
                        logger.info(f"Agent triggered successfully via clawdbot cron wake")
                    else:
                        logger.warning(f"clawdbot cron wake failed: {result.stderr}")
                        
                except subprocess.TimeoutExpired:
                    logger.warning("clawdbot cron wake timed out")
                except FileNotFoundError:
                    logger.warning("clawdbot CLI not found in PATH")
                    
            except Exception as e:
                logger.warning(f"Failed to trigger agent: {e}")
        
        # Run in background thread to not block response
        threading.Thread(target=do_trigger, daemon=True).start()

    @app.post("/chat/send", response_model=ChatResponse)
    async def chat_send(body: ChatMessageBody):
        """
        Receive a chat message from another agent.
        Optionally requires auth_token if configured.
        Automatically triggers local Clawdbot agent to respond.
        """
        try:
            # Verify token if configured
            if not _verify_chat_token(body.auth_token):
                logger.warning(f"Chat rejected: invalid auth token from {body.from_agent}")
                return ChatResponse(success=False, error="Invalid or missing auth token")
            
            my_name = socket.gethostname().lower()
            if "xiaolei" in my_name or "desktop-vn0amd7" in my_name.lower():
                my_name = "xiaolei"
            elif "xiaoxia" in my_name or "lily-virtualbox" in my_name.lower():
                my_name = "xiaoxia"
            
            msg = {
                "timestamp": datetime.now().isoformat(),
                "from_agent": body.from_agent,
                "to_agent": my_name,
                "message": body.message,
                "direction": "incoming",
                "read": False,  # Mark as unread for /chat/check
            }
            _save_chat_message(msg)
            
            msg_id = f"msg_{int(time.time() * 1000)}"
            logger.info(f"Chat received from {body.from_agent}: {body.message[:50]}...")
            
            # Trigger local agent to respond
            _trigger_local_agent(body.from_agent, body.message)
            
            return ChatResponse(success=True, message_id=msg_id)
        except Exception as e:
            logger.error(f"Chat error: {e}")
            return ChatResponse(success=False, error=str(e))

    @app.post("/chat/save_outgoing", response_model=ChatResponse)
    async def chat_save_outgoing(body: ChatMessageBody):
        """
        Save an outgoing message to local chat history.
        Called by sender after successfully sending to recipient.
        """
        try:
            my_name = socket.gethostname().lower()
            if "xiaolei" in my_name or "desktop-vn0amd7" in my_name.lower():
                my_name = "xiaolei"
            elif "xiaoxia" in my_name or "lily-virtualbox" in my_name.lower():
                my_name = "xiaoxia"
            
            # body.from_agent is actually "me" (the sender)
            # body.message contains the message we sent
            # We need to figure out who we sent it to
            to_agent = "xiaoxia" if my_name == "xiaolei" else "xiaolei"
            
            msg = {
                "timestamp": datetime.now().isoformat(),
                "from_agent": my_name,
                "to_agent": to_agent,
                "message": body.message,
                "direction": "outgoing",
            }
            _save_chat_message(msg)
            
            msg_id = f"out_{int(time.time() * 1000)}"
            logger.info(f"Outgoing message saved: {body.message[:50]}...")
            
            return ChatResponse(success=True, message_id=msg_id)
        except Exception as e:
            logger.error(f"Save outgoing error: {e}")
            return ChatResponse(success=False, error=str(e))

    @app.get("/chat/history", response_model=ChatHistoryResponse)
    async def chat_history(limit: int = 50):
        """Get recent chat history."""
        history = _load_chat_history()
        recent = history[-limit:] if len(history) > limit else history
        return ChatHistoryResponse(
            messages=[ChatMessage(**m) for m in recent],
            count=len(recent),
        )

    @app.get("/chat/check")
    async def chat_check():
        """
        Check for unread incoming messages.
        Returns unread messages and marks them as read.
        This is the lightweight interrupt mechanism for sister chat.
        """
        history = _load_chat_history()
        
        # Find unread incoming messages
        # Only messages with explicit read=False are unread
        # Messages without 'read' field are old messages, treat as read
        unread = []
        updated = False
        for msg in history:
            if msg.get("direction") == "incoming" and msg.get("read") is False:
                unread.append(msg)
                msg["read"] = True
                updated = True
        
        # Save if we marked any as read
        if updated:
            _save_chat_history(history)
        
        return {
            "unread_count": len(unread),
            "messages": unread,
        }

    def _save_chat_history(history: list):
        """Save full chat history back to file."""
        chat_file = _get_chat_file()
        chat_file.parent.mkdir(parents=True, exist_ok=True)
        with open(chat_file, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    @app.get("/screen/info", response_model=ScreenInfoResponse)
    async def screen_info():
        """Get screen information - no authentication required."""
        try:
            info = get_primary_monitor_info()
            return ScreenInfoResponse(
                width=info.width,
                height=info.height,
                dpi=info.dpi,
            )
        except Exception as e:
            logger.warning("Failed to get screen info: %s", e)
            # Fallback to common defaults
            return ScreenInfoResponse(
                width=1920,
                height=1080,
                dpi=96,
            )

    @app.get("/screen/capture")
    async def screen_capture(quality: int = 50, scale: float = 0.5):
        """
        Capture a screenshot and return as base64 JPEG.
        
        Args:
            quality: JPEG quality (1-100), default 50
            scale: Scale factor (0.1-1.0), default 0.5
        """
        try:
            if _frame_streamer and _frame_streamer.capture:
                # Use existing capture - returns (b64_data, metrics)
                b64_data, metrics = _frame_streamer.capture.capture_base64(
                    quality=min(max(quality, 1), 100),
                    scale=min(max(scale, 0.1), 1.0),
                )
                return {"image": b64_data, "format": "jpeg", "size_bytes": metrics.frame_size_bytes}
            else:
                raise HTTPException(status_code=503, detail="Screen capture not available")
        except Exception as e:
            logger.error("Screen capture failed: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    # === Command execution endpoint ===
    
    class ExecuteBody(BaseModel):
        """Request body for command execution."""
        command: str
        timeout: int = 30
        shell: bool = True
        cwd: Optional[str] = None

    class ExecuteResponse(BaseModel):
        """Response from command execution."""
        success: bool
        exit_code: int
        stdout: str
        stderr: str
        duration_ms: float

    def _run_blocking_subprocess(command: str, shell: bool, timeout: int, cwd: Optional[str]):
        """
        Synchronous subprocess execution - runs in thread pool to avoid blocking event loop.
        
        This function is intentionally synchronous and will be called via run_in_executor.
        """
        import subprocess
        import time as time_module
        
        start = time_module.time()
        try:
            result = subprocess.run(
                command,
                shell=shell,
                capture_output=True,
                encoding='utf-8',
                errors='ignore',  # Handle encoding errors gracefully
                timeout=timeout,
                cwd=cwd,
            )
            duration = (time_module.time() - start) * 1000
            
            return {
                'success': result.returncode == 0,
                'exit_code': result.returncode,
                'stdout': result.stdout[:50000] if result.stdout else '',
                'stderr': result.stderr[:10000] if result.stderr else '',
                'duration_ms': duration,
            }
        except subprocess.TimeoutExpired:
            duration = (time_module.time() - start) * 1000
            return {
                'success': False,
                'exit_code': -1,
                'stdout': '',
                'stderr': f'Command timed out after {timeout}s',
                'duration_ms': duration,
            }
        except Exception as e:
            duration = (time_module.time() - start) * 1000
            return {
                'success': False,
                'exit_code': -1,
                'stdout': '',
                'stderr': str(e),
                'duration_ms': duration,
            }

    @app.post("/execute", response_model=ExecuteResponse)
    async def execute_command(body: ExecuteBody):
        """
        Execute a shell command on the target PC.
        
        Uses run_in_executor to avoid blocking the event loop.
        
        Args:
            command: Command to execute
            timeout: Timeout in seconds (default 30)
            shell: Run in shell (default True)
            cwd: Working directory (optional)
        
        Returns:
            Command output and exit code
        """
        from functools import partial
        
        loop = asyncio.get_running_loop()
        
        # Run blocking subprocess in thread pool
        func = partial(_run_blocking_subprocess, body.command, body.shell, body.timeout, body.cwd)
        result = await loop.run_in_executor(None, func)
        
        return ExecuteResponse(
            success=result['success'],
            exit_code=result['exit_code'],
            stdout=result['stdout'],
            stderr=result['stderr'],
            duration_ms=result['duration_ms'],
        )

    # === Pairing endpoints (no auth required) ===

    @app.post("/pair/request")
    async def pair_request(request: Request, body: PairRequestBody):
        """
        Request pairing with the agent.

        Returns a challenge that must be signed with the client's private key.
        """
        client_ip = _get_client_ip(request)

        # Check pairing rate limit
        allowed, retry_after = await _rate_limiter.check_pairing_limit(client_ip)
        if not allowed:
            _audit_logger.log_rate_limited(
                client_id=body.client_id,
                ip_address=client_ip,
                endpoint="/pair/request",
                limit_type="pairing",
            )
            raise HTTPException(
                status_code=429,
                detail=f"Too many pairing requests. Retry after {retry_after:.0f}s",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        try:
            # Audit log pairing request
            _audit_logger.log_pairing_request(
                client_id=body.client_id,
                client_name=body.client_name,
                ip_address=client_ip,
            )

            result = _pairing_manager.create_pairing_request(
                client_id=body.client_id,
                client_name=body.client_name,
                client_public_key_pem=body.public_key,
            )
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/pair/confirm")
    async def pair_confirm(request: Request, body: PairConfirmBody):
        """
        Confirm pairing with a signed challenge.

        Returns a token for future authentication if successful.
        """
        client_ip = _get_client_ip(request)

        try:
            signed_challenge = base64.b64decode(body.signed_challenge)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid signed_challenge encoding")

        result = _pairing_manager.confirm_pairing(
            client_id=body.client_id,
            signed_challenge=signed_challenge,
        )

        if result is None:
            # Audit log rejected pairing
            _audit_logger.log_pairing_result(
                client_id=body.client_id,
                client_name="Unknown",
                approved=False,
                ip_address=client_ip,
            )
            raise HTTPException(
                status_code=401,
                detail="Pairing failed - challenge expired, rejected, or invalid signature",
            )

        # Audit log approved pairing
        paired_client = _pairing_manager.get_client(body.client_id)
        client_name = paired_client.client_name if paired_client else "Unknown"
        _audit_logger.log_pairing_result(
            client_id=body.client_id,
            client_name=client_name,
            approved=True,
            ip_address=client_ip,
        )

        return result

    # === Session endpoints (auth required) ===

    @app.post("/session/start", response_model=SessionResponse)
    async def session_start(request: Request, client_id: str = Depends(verify_token)):
        """Start a control session."""
        # Check kill switch
        if _kill_switch_state.active:
            raise HTTPException(
                status_code=503,
                detail="Kill switch is active - new sessions are blocked",
            )

        try:
            session = _session_manager.start_session(client_id)

            # Get client info for logging and indicator
            client_ip = _get_client_ip(request)
            paired_client = _pairing_manager.get_client(client_id)
            client_name = paired_client.client_name if paired_client else client_id[:16]

            # Audit log session start
            _audit_logger.log_session_start(
                client_id=client_id,
                session_id=session.session_id,
                ip_address=client_ip,
            )

            # Notify about session change (for tray indicator)
            active_count = len(_session_manager.get_active_sessions())
            if _on_session_change_callback:
                try:
                    _on_session_change_callback(active_count, client_name)
                except Exception:
                    pass

            return SessionResponse(
                session_id=session.session_id,
                client_id=session.client_id,
                started_at=session.started_at,
                command_count=session.command_count,
            )
        except SessionAlreadyActiveError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @app.post("/session/end", response_model=SessionEndResponse)
    async def session_end(
        body: SessionEndBody,
        client_id: str = Depends(verify_token),
    ):
        """End a control session."""
        try:
            session = _session_manager.end_session(body.session_id, client_id)

            # Cancel pending commands for this session
            _command_queue.cancel_session_commands(body.session_id)

            duration = (session.ended_at or time.time()) - session.started_at

            # Audit log session end
            _audit_logger.log_session_end(
                client_id=client_id,
                session_id=body.session_id,
                duration=duration,
                commands_executed=session.command_count,
                reason="normal",
            )

            # Notify about session change (for tray indicator)
            active_count = len(_session_manager.get_active_sessions())
            if _on_session_change_callback:
                try:
                    _on_session_change_callback(active_count, None)
                except Exception:
                    pass

            return SessionEndResponse(
                ended=True,
                session_id=session.session_id,
                duration=duration,
                commands_executed=session.command_count,
            )
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except SessionNotActiveError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except SessionError as e:
            raise HTTPException(status_code=403, detail=str(e))

    # === Command endpoints (auth required) ===

    @app.post("/commands", response_model=CommandsResponse)
    async def submit_commands(
        request: Request,
        body: SubmitCommandsBody,
        client_id: str = Depends(verify_token),
    ):
        """Submit commands to the queue."""
        # Check kill switch
        if _kill_switch_state.active:
            raise HTTPException(
                status_code=503,
                detail="Kill switch is active - commands are blocked",
            )

        # Check command rate limit
        allowed, retry_after = await _rate_limiter.check_command_limit(body.session_id)
        if not allowed:
            client_ip = _get_client_ip(request)
            _audit_logger.log_rate_limited(
                client_id=client_id,
                ip_address=client_ip,
                endpoint="/commands",
                limit_type="commands",
            )
            raise HTTPException(
                status_code=429,
                detail=f"Command rate limit exceeded. Retry after {retry_after:.2f}s",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

        # Validate session
        try:
            session = _session_manager.validate_session(body.session_id, client_id)
        except SessionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except SessionNotActiveError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except SessionError as e:
            raise HTTPException(status_code=403, detail=str(e))

        # Submit commands
        try:
            queued_ids = await _command_queue.submit(
                commands=body.commands,
                session_id=body.session_id,
            )

            # Update session command count and audit log each command
            client_ip = _get_client_ip(request)
            for cmd_data in body.commands:
                cmd_id = cmd_data.get("id", "unknown")
                cmd_type = cmd_data.get("type", "unknown")
                _session_manager.increment_command_count(body.session_id)

                # Audit log command submission
                _audit_logger.log_command_submitted(
                    client_id=client_id,
                    session_id=body.session_id,
                    command_id=cmd_id,
                    command_type=cmd_type,
                    params={k: v for k, v in cmd_data.items() if k not in ("id", "type")},
                    ip_address=client_ip,
                )

            return CommandsResponse(queued=queued_ids)
        except InvalidCommandError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except CommandError as e:
            raise HTTPException(status_code=503, detail=str(e))

    @app.get("/commands/{command_id}", response_model=CommandStatusResponse)
    async def get_command_status(
        command_id: str,
        client_id: str = Depends(verify_token),
    ):
        """Get command status."""
        try:
            command = _command_queue.get_status(command_id)
            return CommandStatusResponse(
                id=command.id,
                type=command.type.value,
                status=command.status.value,
                result=command.result.data if command.result else None,
                error=command.result.error if command.result else None,
            )
        except CommandNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

    # === Kill Switch endpoints ===

    @app.post("/kill-switch/activate", response_model=KillSwitchResponse)
    async def activate_kill_switch(request: Request, client_id: str = Depends(verify_token)):
        """
        Activate the kill switch to terminate all sessions and block new ones.

        This is an emergency endpoint to immediately stop all remote control.
        """
        global _kill_switch_state

        client_ip = _get_client_ip(request)

        # Get current active sessions before termination
        active_sessions = _session_manager.get_active_sessions()
        session_count = len(active_sessions)

        # Log session ends for each terminated session
        for session in active_sessions:
            duration = time.time() - session.started_at
            _audit_logger.log_session_end(
                client_id=session.client_id,
                session_id=session.session_id,
                duration=duration,
                commands_executed=session.command_count,
                reason="kill_switch",
            )

        # Force end all sessions
        terminated = _session_manager.force_end_all_sessions()

        # Activate kill switch
        _kill_switch_state = KillSwitchState(
            active=True,
            activated_at=time.time(),
            activated_by=client_id,
            reason="API activation",
        )

        # Audit log kill switch activation
        _audit_logger.log_kill_switch(
            sessions_terminated=terminated,
            triggered_by=f"api:{client_id}",
            ip_address=client_ip,
        )

        # Notify about session change (clear tray indicator)
        if _on_session_change_callback:
            try:
                _on_session_change_callback(0, None)
            except Exception:
                pass

        # Broadcast kill switch event to all connected WebSockets
        await broadcast_event("kill_switch", {
            "activated": True,
            "sessions_terminated": terminated,
        })

        return KillSwitchResponse(
            activated=True,
            sessions_terminated=terminated,
            message=f"Kill switch activated. {terminated} session(s) terminated.",
        )

    @app.post("/kill-switch/deactivate", response_model=KillSwitchResponse)
    async def deactivate_kill_switch(request: Request, client_id: str = Depends(verify_token)):
        """
        Deactivate the kill switch to allow new sessions.
        """
        global _kill_switch_state

        if not _kill_switch_state.active:
            return KillSwitchResponse(
                activated=False,
                sessions_terminated=0,
                message="Kill switch was not active.",
            )

        _kill_switch_state = KillSwitchState(active=False)

        # Broadcast deactivation
        await broadcast_event("kill_switch", {
            "activated": False,
            "sessions_terminated": 0,
        })

        return KillSwitchResponse(
            activated=False,
            sessions_terminated=0,
            message="Kill switch deactivated. New sessions are now allowed.",
        )

    @app.get("/kill-switch/status")
    async def kill_switch_status(client_id: str = Depends(verify_token)):
        """Get the current kill switch status."""
        return {
            "active": _kill_switch_state.active,
            "activated_at": _kill_switch_state.activated_at,
            "activated_by": _kill_switch_state.activated_by,
            "reason": _kill_switch_state.reason,
        }

    # === WebSocket endpoint (auth via query param) ===

    @app.websocket("/events")
    async def websocket_events(websocket: WebSocket):
        """
        WebSocket endpoint for real-time events and frame streaming.

        Clients can send JSON commands to control streaming:
        - {"action": "start_streaming"} - Start receiving frames
        - {"action": "stop_streaming"} - Stop receiving frames
        - {"action": "set_fps", "fps": 5} - Set target FPS (2-10)
        - {"action": "set_quality", "quality": 70} - Set quality (30-90)
        - {"action": "capture_frame"} - Request single frame immediately
        - "ping" - Keepalive ping (responds with "pong")
        """
        # Verify token from query parameter
        try:
            client_id = await verify_websocket_token(websocket)
        except Exception:
            await websocket.close(code=1008, reason="Authentication required")
            return

        await websocket.accept()
        _connected_websockets.add(websocket)

        try:
            while True:
                # Keep connection alive and handle incoming messages
                try:
                    message = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=30.0,
                    )

                    # Handle ping/pong for keepalive
                    if message == "ping":
                        await websocket.send_text("pong")
                        continue

                    # Try to parse as JSON command
                    try:
                        cmd = json.loads(message)
                        action = cmd.get("action")

                        if action == "start_streaming":
                            _streaming_clients.add(websocket)
                            # Start streamer if not already running
                            if _frame_streamer and _streaming_clients:
                                await _frame_streamer.start()
                            await websocket.send_text(json.dumps({
                                "type": "streaming_started",
                                "fps": _frame_streamer.target_fps if _frame_streamer else 0,
                                "quality": _frame_streamer.get_quality() if _frame_streamer else 0,
                            }))

                        elif action == "stop_streaming":
                            _streaming_clients.discard(websocket)
                            # Stop streamer if no more clients
                            if _frame_streamer and not _streaming_clients:
                                await _frame_streamer.stop()
                            await websocket.send_text(json.dumps({
                                "type": "streaming_stopped",
                            }))

                        elif action == "set_fps":
                            fps = cmd.get("fps", 5)
                            if _frame_streamer:
                                _frame_streamer.target_fps = float(fps)
                                await websocket.send_text(json.dumps({
                                    "type": "fps_updated",
                                    "fps": _frame_streamer.target_fps,
                                }))

                        elif action == "set_quality":
                            quality = cmd.get("quality", 70)
                            if _frame_streamer:
                                _frame_streamer.set_quality(int(quality))
                                await websocket.send_text(json.dumps({
                                    "type": "quality_updated",
                                    "quality": _frame_streamer.get_quality(),
                                }))

                        elif action == "capture_frame":
                            # Capture and send a single frame immediately
                            if _frame_streamer:
                                b64_data, metrics = await _frame_streamer.capture_single_frame()
                                await websocket.send_text(json.dumps({
                                    "type": "frame",
                                    "data": b64_data,
                                    "timestamp": metrics.timestamp,
                                    "quality": metrics.quality,
                                    "size_bytes": metrics.frame_size_bytes,
                                }))

                        elif action == "get_status":
                            await websocket.send_text(json.dumps({
                                "type": "status",
                                "streaming": websocket in _streaming_clients,
                                "fps": _frame_streamer.target_fps if _frame_streamer else 0,
                                "quality": _frame_streamer.get_quality() if _frame_streamer else 0,
                                "streaming_active": _frame_streamer._running if _frame_streamer else False,
                                "connected_clients": len(_connected_websockets),
                                "streaming_clients": len(_streaming_clients),
                            }))

                        else:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": f"Unknown action: {action}",
                            }))

                    except json.JSONDecodeError:
                        # Not JSON, ignore non-ping messages
                        pass

                except asyncio.TimeoutError:
                    # Send keepalive
                    try:
                        await websocket.send_text(json.dumps({"type": "keepalive"}))
                    except Exception:
                        break
        except WebSocketDisconnect:
            pass
        finally:
            _connected_websockets.discard(websocket)
            _streaming_clients.discard(websocket)
            # Stop streamer if no more streaming clients
            if _frame_streamer and not _streaming_clients:
                await _frame_streamer.stop()

    return app


def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    if _session_manager is None:
        raise RuntimeError("Session manager not initialized")
    return _session_manager


def get_command_queue() -> CommandQueue:
    """Get the global command queue instance."""
    if _command_queue is None:
        raise RuntimeError("Command queue not initialized")
    return _command_queue


def get_frame_streamer() -> FrameStreamer:
    """Get the global frame streamer instance."""
    if _frame_streamer is None:
        raise RuntimeError("Frame streamer not initialized")
    return _frame_streamer


def get_frame_config() -> FrameStreamConfig:
    """Get the frame streaming configuration."""
    if _frame_config is None:
        raise RuntimeError("Frame config not initialized")
    return _frame_config
