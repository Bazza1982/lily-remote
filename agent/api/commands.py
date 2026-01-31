"""Command queue for Lily Remote Agent.

Handles command submission, validation, execution via Win32 SendInput,
and read-back verification of input operations.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

from ..control import input as input_control
from ..control import verify as input_verify

logger = logging.getLogger(__name__)


class CommandStatus(Enum):
    """Status of a command in the queue."""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CommandType(Enum):
    """Types of commands that can be executed."""
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    MOVE = "move"
    DRAG = "drag"
    TYPE = "type"
    HOTKEY = "hotkey"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    KEY_PRESS = "key_press"
    SCROLL = "scroll"


@dataclass
class CommandResult:
    """Result of a command execution."""
    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    executed_at: Optional[float] = None


@dataclass
class Command:
    """Represents a command in the queue."""
    id: str
    type: CommandType
    session_id: str
    params: dict[str, Any]
    status: CommandStatus = CommandStatus.QUEUED
    result: Optional[CommandResult] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert command to dictionary for API response."""
        response = {
            "id": self.id,
            "type": self.type.value,
            "status": self.status.value,
            "created_at": self.created_at,
        }
        if self.result:
            response["result"] = self.result.data
            response["error"] = self.result.error
        return response


class CommandError(Exception):
    """Base exception for command errors."""
    pass


class CommandNotFoundError(CommandError):
    """Raised when a command is not found."""
    pass


class InvalidCommandError(CommandError):
    """Raised when a command is invalid."""
    pass


class CommandQueue:
    """
    Thread-safe command queue for the Lily Remote Agent.

    Commands are submitted in batches, processed sequentially,
    and results are tracked by command ID.
    """

    MAX_QUEUE_SIZE = 1000
    COMMAND_TIMEOUT = 30.0  # seconds

    def __init__(self):
        """Initialize the command queue."""
        self._queue: asyncio.Queue[Command] = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._commands: dict[str, Command] = {}
        self._executor: Optional[Callable[[Command], Coroutine[Any, Any, CommandResult]]] = None
        self._processing = False
        self._process_task: Optional[asyncio.Task] = None
        self._event_callback: Optional[Callable[[str, dict], Coroutine[Any, Any, None]]] = None

    def set_executor(
        self,
        executor: Callable[[Command], Coroutine[Any, Any, CommandResult]],
    ) -> None:
        """
        Set the command executor function.

        Args:
            executor: Async function that executes a command and returns a result.
        """
        self._executor = executor

    def set_event_callback(
        self,
        callback: Callable[[str, dict], Coroutine[Any, Any, None]],
    ) -> None:
        """
        Set callback for command completion events.

        Args:
            callback: Async function called with (event_type, event_data) when command completes.
        """
        self._event_callback = callback

    async def submit(
        self,
        commands: list[dict[str, Any]],
        session_id: str,
    ) -> list[str]:
        """
        Submit a batch of commands to the queue.

        Args:
            commands: List of command dictionaries with id, type, and params.
            session_id: The session ID for authorization.

        Returns:
            List of queued command IDs.

        Raises:
            InvalidCommandError: If a command is invalid.
            CommandError: If the queue is full.
        """
        queued_ids = []

        for cmd_data in commands:
            # Validate required fields
            cmd_id = cmd_data.get("id")
            cmd_type_str = cmd_data.get("type")

            if not cmd_id:
                raise InvalidCommandError("Command missing 'id' field")
            if not cmd_type_str:
                raise InvalidCommandError(f"Command {cmd_id} missing 'type' field")

            # Validate command type
            try:
                cmd_type = CommandType(cmd_type_str)
            except ValueError:
                raise InvalidCommandError(
                    f"Invalid command type '{cmd_type_str}' for command {cmd_id}"
                )

            # Validate command parameters
            self._validate_command_params(cmd_id, cmd_type, cmd_data)

            # Create command object
            command = Command(
                id=cmd_id,
                type=cmd_type,
                session_id=session_id,
                params=self._extract_params(cmd_type, cmd_data),
            )

            # Check for duplicate ID
            if cmd_id in self._commands:
                raise InvalidCommandError(f"Duplicate command ID: {cmd_id}")

            # Add to queue
            try:
                self._queue.put_nowait(command)
            except asyncio.QueueFull:
                raise CommandError("Command queue is full")

            self._commands[cmd_id] = command
            queued_ids.append(cmd_id)

        return queued_ids

    def _validate_command_params(
        self,
        cmd_id: str,
        cmd_type: CommandType,
        cmd_data: dict[str, Any],
    ) -> None:
        """Validate command parameters based on type."""
        if cmd_type in (
            CommandType.CLICK,
            CommandType.DOUBLE_CLICK,
            CommandType.RIGHT_CLICK,
            CommandType.MOVE,
        ):
            if "x" not in cmd_data or "y" not in cmd_data:
                raise InvalidCommandError(
                    f"Command {cmd_id} of type {cmd_type.value} requires 'x' and 'y' coordinates"
                )
            if not isinstance(cmd_data["x"], (int, float)):
                raise InvalidCommandError(f"Command {cmd_id}: 'x' must be a number")
            if not isinstance(cmd_data["y"], (int, float)):
                raise InvalidCommandError(f"Command {cmd_id}: 'y' must be a number")

        elif cmd_type == CommandType.TYPE:
            if "text" not in cmd_data:
                raise InvalidCommandError(
                    f"Command {cmd_id} of type 'type' requires 'text' field"
                )
            if not isinstance(cmd_data["text"], str):
                raise InvalidCommandError(f"Command {cmd_id}: 'text' must be a string")

        elif cmd_type == CommandType.HOTKEY:
            if "keys" not in cmd_data:
                raise InvalidCommandError(
                    f"Command {cmd_id} of type 'hotkey' requires 'keys' field"
                )
            if not isinstance(cmd_data["keys"], list):
                raise InvalidCommandError(f"Command {cmd_id}: 'keys' must be a list")
            if len(cmd_data["keys"]) == 0:
                raise InvalidCommandError(f"Command {cmd_id}: 'keys' cannot be empty")

        elif cmd_type in (CommandType.KEY_DOWN, CommandType.KEY_UP):
            if "key" not in cmd_data:
                raise InvalidCommandError(
                    f"Command {cmd_id} of type '{cmd_type.value}' requires 'key' field"
                )

        elif cmd_type == CommandType.SCROLL:
            if "delta" not in cmd_data:
                raise InvalidCommandError(
                    f"Command {cmd_id} of type 'scroll' requires 'delta' field"
                )

        elif cmd_type == CommandType.DRAG:
            required = ["start_x", "start_y", "end_x", "end_y"]
            for field in required:
                if field not in cmd_data:
                    raise InvalidCommandError(
                        f"Command {cmd_id} of type 'drag' requires '{field}' field"
                    )
                if not isinstance(cmd_data[field], (int, float)):
                    raise InvalidCommandError(f"Command {cmd_id}: '{field}' must be a number")

        elif cmd_type == CommandType.KEY_PRESS:
            if "key" not in cmd_data:
                raise InvalidCommandError(
                    f"Command {cmd_id} of type 'key_press' requires 'key' field"
                )

    def _extract_params(
        self,
        cmd_type: CommandType,
        cmd_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract relevant parameters based on command type."""
        params: dict[str, Any] = {}

        if cmd_type in (
            CommandType.CLICK,
            CommandType.DOUBLE_CLICK,
            CommandType.RIGHT_CLICK,
            CommandType.MOVE,
        ):
            params["x"] = int(cmd_data["x"])
            params["y"] = int(cmd_data["y"])
            if cmd_type == CommandType.CLICK:
                params["button"] = cmd_data.get("button", "left")

        elif cmd_type == CommandType.TYPE:
            params["text"] = cmd_data["text"]
            params["interval"] = cmd_data.get("interval", 0.0)

        elif cmd_type == CommandType.HOTKEY:
            params["keys"] = cmd_data["keys"]

        elif cmd_type in (CommandType.KEY_DOWN, CommandType.KEY_UP):
            params["key"] = cmd_data["key"]

        elif cmd_type == CommandType.SCROLL:
            params["delta"] = int(cmd_data["delta"])
            params["x"] = int(cmd_data.get("x", 0)) if cmd_data.get("x") is not None else None
            params["y"] = int(cmd_data.get("y", 0)) if cmd_data.get("y") is not None else None
            params["horizontal"] = cmd_data.get("horizontal", False)

        elif cmd_type == CommandType.DRAG:
            params["start_x"] = int(cmd_data["start_x"])
            params["start_y"] = int(cmd_data["start_y"])
            params["end_x"] = int(cmd_data["end_x"])
            params["end_y"] = int(cmd_data["end_y"])
            params["button"] = cmd_data.get("button", "left")
            params["duration"] = cmd_data.get("duration", 0.5)
            params["steps"] = cmd_data.get("steps", 20)

        elif cmd_type == CommandType.KEY_PRESS:
            params["key"] = cmd_data["key"]

        return params

    def get_status(self, command_id: str) -> Command:
        """
        Get the status of a command.

        Args:
            command_id: The command's unique identifier.

        Returns:
            The Command object.

        Raises:
            CommandNotFoundError: If the command is not found.
        """
        command = self._commands.get(command_id)
        if not command:
            raise CommandNotFoundError(f"Command {command_id} not found")
        return command

    async def execute(self, command: Command) -> CommandResult:
        """
        Execute a single command.

        Args:
            command: The command to execute.

        Returns:
            The result of the command execution.
        """
        if not self._executor:
            return CommandResult(
                success=False,
                error="No executor configured",
                executed_at=time.time(),
            )

        command.status = CommandStatus.RUNNING
        command.started_at = time.time()

        try:
            result = await asyncio.wait_for(
                self._executor(command),
                timeout=self.COMMAND_TIMEOUT,
            )
        except asyncio.TimeoutError:
            result = CommandResult(
                success=False,
                error=f"Command timed out after {self.COMMAND_TIMEOUT}s",
                executed_at=time.time(),
            )
        except Exception as e:
            result = CommandResult(
                success=False,
                error=str(e),
                executed_at=time.time(),
            )

        command.result = result
        command.completed_at = time.time()
        command.status = CommandStatus.SUCCEEDED if result.success else CommandStatus.FAILED

        # Fire completion event
        if self._event_callback:
            try:
                await self._event_callback(
                    "command_done",
                    {
                        "id": command.id,
                        "status": command.status.value,
                        "result": result.data,
                        "error": result.error,
                    },
                )
            except Exception:
                pass  # Don't fail command due to callback error

        return result

    async def start_processing(self) -> None:
        """Start the background command processing loop."""
        if self._processing:
            return

        self._processing = True
        self._process_task = asyncio.create_task(self._process_loop())

    async def stop_processing(self) -> None:
        """Stop the background command processing loop."""
        self._processing = False
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None

    async def _process_loop(self) -> None:
        """Background loop that processes commands from the queue."""
        while self._processing:
            try:
                # Wait for a command with timeout to allow checking _processing flag
                try:
                    command = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    continue

                await self.execute(command)
                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception:
                # Log error but continue processing
                continue

    def get_pending_count(self) -> int:
        """Get the number of pending commands in the queue."""
        return self._queue.qsize()

    def clear_completed(self, max_age: float = 300.0) -> int:
        """
        Clear completed commands older than max_age seconds.

        Args:
            max_age: Maximum age in seconds for completed commands.

        Returns:
            Number of commands cleared.
        """
        now = time.time()
        to_remove = []

        for cmd_id, command in self._commands.items():
            if command.status in (CommandStatus.SUCCEEDED, CommandStatus.FAILED):
                if command.completed_at and (now - command.completed_at) > max_age:
                    to_remove.append(cmd_id)

        for cmd_id in to_remove:
            del self._commands[cmd_id]

        return len(to_remove)

    def cancel_session_commands(self, session_id: str) -> int:
        """
        Cancel all pending commands for a session.

        Args:
            session_id: The session ID to cancel commands for.

        Returns:
            Number of commands cancelled.
        """
        cancelled = 0
        for command in self._commands.values():
            if (
                command.session_id == session_id
                and command.status == CommandStatus.QUEUED
            ):
                command.status = CommandStatus.FAILED
                command.result = CommandResult(
                    success=False,
                    error="Session ended",
                    executed_at=time.time(),
                )
                command.completed_at = time.time()
                cancelled += 1
        return cancelled


# =============================================================================
# Command Executor - Executes commands using Win32 SendInput
# =============================================================================

class CommandExecutor:
    """
    Executes commands using Win32 SendInput with read-back verification.

    This class provides the actual implementation of input commands
    and should be set as the executor for the CommandQueue.
    """

    def __init__(self):
        """Initialize the command executor."""
        self._controller = None
        self._input_error = None
        
        if input_control.is_input_available():
            try:
                self._controller = input_control.get_controller()
            except Exception as e:
                self._input_error = str(e)
                logger.warning(f"Input control not available: {e}")
        else:
            self._input_error = input_control.get_input_error()
            logger.warning(f"Input control not available: {self._input_error}")

    async def execute(self, command: Command) -> CommandResult:
        """
        Execute a command and return the result.

        Args:
            command: The command to execute.

        Returns:
            CommandResult with success status and verification data.
        """
        # Check if input control is available
        if self._controller is None:
            return CommandResult(
                success=False,
                error=f"Input control not available: {self._input_error or 'Unknown error'}",
                executed_at=time.time(),
            )
        
        try:
            # Execute the command based on type
            handler = self._get_handler(command.type)
            if not handler:
                return CommandResult(
                    success=False,
                    error=f"Unknown command type: {command.type.value}",
                    executed_at=time.time(),
                )

            # Run the command (blocking operations run in thread pool)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                handler,
                command.params,
            )

            return result

        except Exception as e:
            logger.error("Command execution failed: %s", e)
            return CommandResult(
                success=False,
                error=str(e),
                executed_at=time.time(),
            )

    def _get_handler(self, cmd_type: CommandType):
        """Get the handler function for a command type."""
        handlers = {
            CommandType.CLICK: self._handle_click,
            CommandType.DOUBLE_CLICK: self._handle_double_click,
            CommandType.RIGHT_CLICK: self._handle_right_click,
            CommandType.MOVE: self._handle_move,
            CommandType.DRAG: self._handle_drag,
            CommandType.TYPE: self._handle_type,
            CommandType.HOTKEY: self._handle_hotkey,
            CommandType.KEY_DOWN: self._handle_key_down,
            CommandType.KEY_UP: self._handle_key_up,
            CommandType.KEY_PRESS: self._handle_key_press,
            CommandType.SCROLL: self._handle_scroll,
        }
        return handlers.get(cmd_type)

    def _handle_click(self, params: dict[str, Any]) -> CommandResult:
        """Handle click command."""
        x = params["x"]
        y = params["y"]
        button = params.get("button", "left")

        result = self._controller.click(x, y, button=button)

        # Verify and return
        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_double_click(self, params: dict[str, Any]) -> CommandResult:
        """Handle double-click command."""
        x = params["x"]
        y = params["y"]

        result = self._controller.double_click(x, y)

        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_right_click(self, params: dict[str, Any]) -> CommandResult:
        """Handle right-click command."""
        x = params["x"]
        y = params["y"]

        result = self._controller.right_click(x, y)

        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_move(self, params: dict[str, Any]) -> CommandResult:
        """Handle mouse move command."""
        x = params["x"]
        y = params["y"]

        result = self._controller.move(x, y)

        # Verify cursor position
        verify_result = input_verify.verify_cursor_position(x, y)
        verify_data = verify_result.to_dict()

        return CommandResult(
            success=result.success and verify_result.success,
            data=verify_data,
            error=result.error or verify_result.error,
            executed_at=time.time(),
        )

    def _handle_drag(self, params: dict[str, Any]) -> CommandResult:
        """Handle drag command."""
        result = self._controller.drag(
            start_x=params["start_x"],
            start_y=params["start_y"],
            end_x=params["end_x"],
            end_y=params["end_y"],
            button=params.get("button", "left"),
            duration=params.get("duration", 0.5),
            steps=params.get("steps", 20),
        )

        # Verify final cursor position
        verify_result = input_verify.verify_cursor_position(
            params["end_x"], params["end_y"]
        )
        verify_data = verify_result.to_dict()

        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_type(self, params: dict[str, Any]) -> CommandResult:
        """Handle type text command."""
        text = params["text"]
        interval = params.get("interval", 0.0)

        result = self._controller.type_text(text, interval=interval)

        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_hotkey(self, params: dict[str, Any]) -> CommandResult:
        """Handle hotkey command."""
        keys = params["keys"]

        result = self._controller.hotkey(*keys)

        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_key_down(self, params: dict[str, Any]) -> CommandResult:
        """Handle key down command."""
        key = params["key"]

        result = self._controller.key_down(key)

        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_key_up(self, params: dict[str, Any]) -> CommandResult:
        """Handle key up command."""
        key = params["key"]

        result = self._controller.key_up(key)

        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_key_press(self, params: dict[str, Any]) -> CommandResult:
        """Handle key press command."""
        key = params["key"]

        result = self._controller.key_press(key)

        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )

    def _handle_scroll(self, params: dict[str, Any]) -> CommandResult:
        """Handle scroll command."""
        delta = params["delta"]
        x = params.get("x")
        y = params.get("y")
        horizontal = params.get("horizontal", False)

        result = self._controller.scroll(delta, x=x, y=y, horizontal=horizontal)

        verify_data = input_verify.quick_verify()
        return CommandResult(
            success=result.success,
            data=verify_data,
            error=result.error,
            executed_at=time.time(),
        )


def create_command_queue() -> CommandQueue:
    """
    Create a CommandQueue with the default executor configured.

    Returns:
        CommandQueue ready for use with input execution.
    """
    queue = CommandQueue()
    executor = CommandExecutor()
    queue.set_executor(executor.execute)
    return queue
