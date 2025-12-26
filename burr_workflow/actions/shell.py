"""
Shell action for executing commands.

Provides secure shell command execution with proper timeout handling,
signal propagation, and output capture.
"""

import asyncio
import os
import re
import signal
from typing import Any, ClassVar, Optional, TYPE_CHECKING

from .base import AbstractAction, ActionResult
from ..core.errors import WorkflowTimeoutError

if TYPE_CHECKING:
    from ..protocols import ExecutionContext


class ShellAction(AbstractAction):
    """Execute shell commands.

    Supports both string and array command formats:
    - String: `run: "echo hello | grep h"` (uses shell)
    - Array: `run: ["echo", "hello"]` (no shell, safer)

    Features:
    - Process group creation for timeout/interrupt handling
    - Graceful shutdown with SIGTERM before SIGKILL
    - Output capture to memory or file
    - Interactive mode for TTY commands
    
    Security:
    - Array form bypasses shell entirely (RECOMMENDED)
    - String form should use shell_quote filter for variables
    - Warns about unquoted variable interpolation
    """

    action_type: ClassVar[str] = "shell"

    @property
    def reads(self) -> list[str]:
        return []

    @property
    def writes(self) -> list[str]:
        return []

    async def execute(
        self,
        step_config: dict[str, Any],
        context: dict[str, Any],
        exec_context: Optional["ExecutionContext"] = None,
    ) -> ActionResult:
        """Execute the shell command.

        Args:
            step_config: Step configuration with 'run' command
            context: Workflow context
            exec_context: Execution context for shell access

        Returns:
            ActionResult with stdout, stderr, exit_code
        """
        from ..evaluator import ContextEvaluator

        cmd = step_config.get("run")
        if not cmd:
            return ActionResult(
                outputs={},
                outcome="failure",
                error="No 'run' command specified",
            )

        # Get step options
        timeout = step_config.get("timeout", 300)
        interactive = step_config.get("interactive", False)
        capture_mode = step_config.get("capture_mode", "memory")
        env_overrides = step_config.get("env", {})

        # Evaluate expressions in command
        evaluator = ContextEvaluator(context)

        # Build environment
        env = dict(os.environ)
        if "env" in context:
            env.update(context["env"])
        if env_overrides:
            resolved_env = evaluator.resolve_all(env_overrides)
            env.update(resolved_env)

        if interactive:
            # Interactive mode - use exec_context for TTY
            if exec_context:
                exit_code = await exec_context.execute_interactive(
                    str(cmd),
                    env=env,
                )
                return ActionResult(
                    outputs={"exit_code": exit_code},
                    outcome="success" if exit_code == 0 else "failure",
                )
            else:
                return ActionResult(
                    outputs={},
                    outcome="failure",
                    error="Interactive mode requires ExecutionContext",
                )

        try:
            if isinstance(cmd, list):
                # Array form - resolve each argument, no shell (SAFE)
                resolved_args = [str(evaluator.resolve(arg)) for arg in cmd]
                success, stdout, stderr = await self._execute_array(
                    resolved_args, timeout, env
                )
            else:
                # String form - resolve embedded expressions, use shell
                resolved_cmd = evaluator.resolve(cmd)
                self._warn_unquoted_vars(step_config, str(cmd), exec_context)
                success, stdout, stderr = await self._execute_shell(
                    str(resolved_cmd), timeout, env
                )

            # Handle capture mode
            if capture_mode == "file":
                # Write to file instead of storing in state
                outputs = await self._capture_to_file(
                    stdout, stderr, step_config, context
                )
            else:
                outputs = {
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": 0 if success else 1,
                }

            return ActionResult(
                outputs=outputs,
                outcome="success" if success else "failure",
                error=stderr if not success else None,
            )

        except asyncio.TimeoutError:
            raise WorkflowTimeoutError(
                f"Command timed out after {timeout}s",
                timeout_seconds=timeout,
                step_id=step_config.get("id"),
            )
        except Exception as e:
            return ActionResult(
                outputs={"exit_code": -1},
                outcome="failure",
                error=str(e),
                error_type=type(e).__name__,
            )

    async def _execute_shell(
        self,
        cmd: str,
        timeout: int,
        env: dict[str, str],
    ) -> tuple[bool, str, str]:
        """Execute command via shell.

        Args:
            cmd: Shell command string
            timeout: Timeout in seconds
            env: Environment variables

        Returns:
            Tuple of (success, stdout, stderr)
        """
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,  # Create new process group
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return (process.returncode == 0, stdout, stderr)

        except asyncio.TimeoutError:
            await self._kill_process_group(process)
            raise

        except asyncio.CancelledError:
            await self._kill_process_group(process)
            return (False, "", "[INTERRUPTED by user]")

    async def _execute_array(
        self,
        args: list[str],
        timeout: int,
        env: dict[str, str],
    ) -> tuple[bool, str, str]:
        """Execute command without shell (array form).
        
        This is the SAFE execution method - no shell interpretation.

        Args:
            args: Command arguments
            timeout: Timeout in seconds
            env: Environment variables

        Returns:
            Tuple of (success, stdout, stderr)
        """
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return (process.returncode == 0, stdout, stderr)

        except asyncio.TimeoutError:
            await self._kill_process_group(process)
            raise

        except asyncio.CancelledError:
            await self._kill_process_group(process)
            return (False, "", "[INTERRUPTED by user]")

    async def _kill_process_group(
        self,
        process: asyncio.subprocess.Process,
        grace_period: float = 5.0,
    ) -> None:
        """Kill process group with grace period.

        First sends SIGTERM, waits, then SIGKILL if still alive.

        Args:
            process: The subprocess
            grace_period: Seconds to wait before SIGKILL
        """
        try:
            pgid = os.getpgid(process.pid)

            # Graceful shutdown first
            os.killpg(pgid, signal.SIGTERM)

            try:
                await asyncio.wait_for(process.wait(), timeout=grace_period)
            except asyncio.TimeoutError:
                # Force kill
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()

        except ProcessLookupError:
            # Process already dead
            pass

    async def _capture_to_file(
        self,
        stdout: str,
        stderr: str,
        step_config: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Write output to file instead of memory.

        Args:
            stdout: Standard output
            stderr: Standard error
            step_config: Step configuration
            context: Workflow context

        Returns:
            Output dict with file paths
        """
        from pathlib import Path

        step_id = step_config.get("id", "unnamed")
        output_dir = context.get("env", {}).get("OUTPUT_DIR", "/tmp")

        stdout_path = Path(output_dir) / f"{step_id}_stdout.txt"
        stderr_path = Path(output_dir) / f"{step_id}_stderr.txt"

        stdout_path.write_text(stdout)
        stderr_path.write_text(stderr)

        return {
            "stdout_file": str(stdout_path),
            "stderr_file": str(stderr_path),
            "stdout_lines": len(stdout.splitlines()),
            "stderr_lines": len(stderr.splitlines()),
            "exit_code": 0,
        }

    def _warn_unquoted_vars(
        self,
        step_config: dict[str, Any],
        cmd: str,
        exec_context: Optional["ExecutionContext"],
    ) -> None:
        """Warn about unquoted variables in shell commands.

        Args:
            step_config: Step configuration
            cmd: The command string
            exec_context: For logging
        """
        # Pattern matches ${{ ... }} NOT followed by | shell_quote
        pattern = r'\$\{\{(?!.*\|\s*shell_quote).*?\}\}'
        if re.search(pattern, cmd):
            step_id = step_config.get("id", "unnamed")
            warning = (
                f"Step '{step_id}' has unquoted variable in shell command. "
                f"Consider using: ${{{{ var | shell_quote }}}} or array syntax."
            )
            if exec_context:
                exec_context.log("warning", warning, step_id=step_id)
