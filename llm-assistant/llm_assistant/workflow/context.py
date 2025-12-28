"""
Execution context bridge for llm-assistant integration.

This module implements the ExecutionContext protocol from burr_workflow,
bridging workflow actions to llm-assistant's command execution, user
prompting, and logging capabilities.
"""

from typing import Any, Optional, TYPE_CHECKING
import asyncio
import os
import signal
import sys

if TYPE_CHECKING:
    from rich.console import Console


class AssistantExecutionContext:
    """
    ExecutionContext implementation for llm-assistant.
    
    Bridges burr_workflow's execution protocol to llm-assistant's
    session capabilities:
    
    - execute_shell() → session.execute_command() with D-Bus/approval
    - prompt_user() → Rich console prompts with options
    - log() → Rich console output with styling
    
    Also provides process tracking for clean interrupt handling.
    
    Example:
        context = AssistantExecutionContext(
            console=session.console,
            execute_fn=session.execute_command,
            prompt_fn=session.prompt_async,
            working_dir=session.working_dir,
        )
        
        compiler = WorkflowCompiler(exec_context=context)
    """
    
    def __init__(
        self,
        console: "Console",
        execute_fn,  # Callable for command execution
        prompt_fn,   # Callable for user prompts
        working_dir: str = None,
        env: Optional[dict[str, str]] = None,
        on_process_start=None,  # Callback when process starts
        on_process_end=None,    # Callback when process ends
        session=None,  # Reference to parent session for action access
    ):
        """Initialize the execution context.

        Args:
            console: Rich console for output
            execute_fn: Function to execute shell commands
                       Signature: async def execute_fn(cmd, timeout, **kwargs) -> (success, stdout, stderr)
            prompt_fn: Function for user prompts
                      Signature: async def prompt_fn(prompt) -> str
            working_dir: Working directory for commands
            env: Additional environment variables
            on_process_start: Callback when subprocess starts
            on_process_end: Callback when subprocess ends
            session: Parent session object for actions that need session access
                    (e.g., ReportAddAction for pentest findings)
        """
        self.console = console
        self._execute_fn = execute_fn
        self._prompt_fn = prompt_fn
        self.working_dir = working_dir or os.getcwd()
        self.env = env or {}
        self._on_process_start = on_process_start
        self._on_process_end = on_process_end
        self._session = session

        # Process tracking for interruption
        self._current_process = None
        self._current_pgid = None
    
    async def execute_shell(
        self,
        command: str,
        timeout: int = 300,
        env: Optional[dict[str, str]] = None,
        *,
        cwd: Optional[str] = None,
        capture: bool = True,
        interactive: bool = False,
    ) -> tuple[bool, str, str]:
        """Execute a shell command.

        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            env: Additional environment variables
            cwd: Working directory (optional, uses self.working_dir if not set)
            capture: Whether to capture output (vs streaming)
            interactive: Whether command requires user interaction

        Returns:
            Tuple of (success, stdout, stderr)
        """
        # Use provided cwd or fall back to self.working_dir
        working_dir = cwd or self.working_dir

        # Merge environment
        cmd_env = {**os.environ, **self.env}
        if env:
            cmd_env.update(env)
        
        try:
            if self._execute_fn:
                # Use session's execute_command (handles D-Bus, approval, etc.)
                # Note: Session's execute_command is synchronous and only takes command
                # We run it in an executor for async compatibility
                import inspect

                if asyncio.iscoroutinefunction(self._execute_fn):
                    # Async function - call directly
                    result = await self._execute_fn(
                        command,
                        timeout=timeout,
                        env=cmd_env,
                        capture=capture,
                        interactive=interactive,
                    )
                else:
                    # Sync function (like session.execute_command) - run in executor
                    # Session's execute_command only takes command parameter
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        lambda: self._execute_fn(command)
                    )

                # Handle different return formats
                if isinstance(result, tuple):
                    if len(result) == 2:
                        success, output = result
                        return success, output, ""
                    elif len(result) == 3:
                        return result
                else:
                    # Assume it's just the output
                    return True, str(result), ""
            else:
                # Fallback to direct subprocess execution
                return await self._execute_subprocess(
                    command, timeout, cmd_env, capture, working_dir
                )
                
        except asyncio.TimeoutError:
            return False, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return False, "", str(e)
    
    async def _execute_subprocess(
        self,
        command: str,
        timeout: int,
        env: dict[str, str],
        capture: bool,
        working_dir: Optional[str] = None,
    ) -> tuple[bool, str, str]:
        """Direct subprocess execution fallback."""
        try:
            # Start process in new session for clean kill
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE if capture else None,
                stderr=asyncio.subprocess.PIPE if capture else None,
                cwd=working_dir or self.working_dir,
                env=env,
                start_new_session=True,
            )
            
            # Track process for interruption
            self._current_process = process
            try:
                self._current_pgid = os.getpgid(process.pid)
            except (OSError, ProcessLookupError):
                self._current_pgid = None
            
            # Notify callbacks
            if self._on_process_start:
                self._on_process_start(process)
            
            try:
                # Wait for completion with timeout
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
                
                success = process.returncode == 0
                return (
                    success,
                    stdout.decode() if stdout else "",
                    stderr.decode() if stderr else "",
                )
                
            finally:
                self._current_process = None
                self._current_pgid = None
                if self._on_process_end:
                    self._on_process_end()
                    
        except asyncio.TimeoutError:
            # Kill process on timeout
            if self._current_pgid:
                try:
                    os.killpg(self._current_pgid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
            raise
    
    async def prompt_user(
        self,
        prompt: str,
        options: Optional[list[str]] = None,
        default: Optional[str] = None,
    ) -> str:
        """Prompt user for input.
        
        Args:
            prompt: The prompt message
            options: Optional list of choices
            default: Default value if user presses Enter
            
        Returns:
            User's response
        """
        if options:
            # Display numbered options
            self.console.print(f"\n[bold]{prompt}[/]")
            for i, opt in enumerate(options, 1):
                marker = "[cyan]→[/]" if opt == default else " "
                self.console.print(f"  {marker} {i}. {opt}")
            
            if self._prompt_fn:
                if asyncio.iscoroutinefunction(self._prompt_fn):
                    response = await self._prompt_fn("Select option: ")
                else:
                    # Sync prompt function - run in executor
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(
                        None, lambda: self._prompt_fn("Select option: ")
                    )
            else:
                # Fallback to blocking input()
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda: input("Select option: "))
            
            # Parse selection
            response = response.strip()
            if not response and default:
                return default
            
            try:
                idx = int(response) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            except ValueError:
                pass
            
            # Try matching by name
            response_lower = response.lower()
            for opt in options:
                if opt.lower() == response_lower or opt.lower().startswith(response_lower):
                    return opt
            
            # Return raw if no match
            return response
        else:
            # Simple prompt
            prompt_text = prompt
            if default:
                prompt_text = f"{prompt} [{default}]"

            if self._prompt_fn:
                if asyncio.iscoroutinefunction(self._prompt_fn):
                    response = await self._prompt_fn(f"{prompt_text}: ")
                else:
                    loop = asyncio.get_event_loop()
                    response = await loop.run_in_executor(
                        None, lambda: self._prompt_fn(f"{prompt_text}: ")
                    )
            else:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None, lambda: input(f"{prompt_text}: ")
                )

            return response.strip() or default or ""
    
    async def execute_interactive(
        self,
        command: str,
        *,
        env: Optional[dict[str, str]] = None,
    ) -> int:
        """Run command in foreground with full TTY control.

        Used for interactive commands (sudo, ssh, vim, etc.)
        where the user needs direct terminal access.

        Args:
            command: Command to run interactively
            env: Optional environment variables

        Returns:
            Exit code when command finishes
        """
        import subprocess

        # Merge environment
        cmd_env = {**os.environ, **self.env}
        if env:
            cmd_env.update(env)

        # Run synchronously with full TTY access
        loop = asyncio.get_event_loop()

        def run_interactive():
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    env=cmd_env,
                    cwd=self.working_dir,
                )
                return result.returncode
            except Exception:
                return 1

        return await loop.run_in_executor(None, run_interactive)

    async def confirm(
        self,
        prompt: str,
        *,
        default: bool = False,
    ) -> bool:
        """Prompt user for yes/no confirmation.

        Args:
            prompt: The confirmation message
            default: Default value (False = No)

        Returns:
            True if confirmed, False otherwise
        """
        default_hint = "[Y/n]" if default else "[y/N]"
        full_prompt = f"{prompt} {default_hint}"

        if self._prompt_fn:
            if asyncio.iscoroutinefunction(self._prompt_fn):
                response = await self._prompt_fn(f"{full_prompt}: ")
            else:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None, lambda: self._prompt_fn(f"{full_prompt}: ")
                )
        else:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda: input(f"{full_prompt}: ")
            )

        response = response.strip().lower()

        if not response:
            return default

        return response in ("y", "yes", "true", "1")

    def log(
        self,
        level: str,
        message: str,
        *,
        step_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Log a message to the console.

        Args:
            level: Log level (debug, info, warning, error)
            message: Message to log
            step_id: Optional step ID for context
            **kwargs: Additional context (ignored for now)
        """
        styles = {
            "debug": "dim",
            "info": "",
            "warning": "yellow",
            "error": "red bold",
            "success": "green",
        }

        style = styles.get(level, "")

        # Add level prefix for non-info messages
        if level != "info":
            prefix = f"[{level.upper()}] "
        else:
            prefix = ""

        # Add step_id context if provided
        if step_id:
            prefix = f"[{step_id}] {prefix}"

        if style:
            self.console.print(f"[{style}]{prefix}{message}[/]")
        else:
            self.console.print(f"{prefix}{message}")
    
    def terminate_current_process(self, graceful_timeout: float = 2.0) -> bool:
        """Terminate the currently running process if any.
        
        Args:
            graceful_timeout: Seconds to wait before SIGKILL
            
        Returns:
            True if a process was terminated
        """
        if not self._current_process:
            return False
        
        pid = self._current_process.pid
        pgid = self._current_pgid
        
        # Try SIGTERM first (graceful)
        if pgid and sys.platform != "win32":
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    return False
        else:
            try:
                self._current_process.terminate()
            except ProcessLookupError:
                return False
        
        # Wait briefly for graceful shutdown
        import time
        start = time.time()
        while time.time() - start < graceful_timeout:
            if self._current_process.returncode is not None:
                return True
            time.sleep(0.1)
        
        # Force kill if still running
        if self._current_process.returncode is None:
            if pgid and sys.platform != "win32":
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
            else:
                try:
                    self._current_process.kill()
                except ProcessLookupError:
                    pass
        
        return True
    
    @property
    def has_active_process(self) -> bool:
        """Check if there's an active subprocess."""
        return (
            self._current_process is not None and
            self._current_process.returncode is None
        )
