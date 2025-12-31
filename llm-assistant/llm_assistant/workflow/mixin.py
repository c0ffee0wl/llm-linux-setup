"""
WorkflowMixin for llm-assistant session integration.

This mixin adds workflow execution capabilities to the TerminatorAssistantSession,
providing:
- /workflow slash commands for workflow control
- Dynamic system prompt injection with workflow context
- Interrupt handling with clean state preservation
- Integration with the session's command execution

Usage:
    class TerminatorAssistantSession(
        KnowledgeBaseMixin, MemoryMixin, RAGMixin, SkillsMixin,
        WorkflowMixin,  # Add as 11th mixin
        ReportMixin, WebMixin, TerminalMixin, ContextMixin,
        WatchMixin, MCPMixin
    ):
        pass
"""

import asyncio
import os
import signal
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console
    from burr_workflow.actions.registry import ActionRegistry


# Maximum characters for step outputs in system prompt
MAX_OUTPUT_SUMMARY_CHARS = 500
MAX_TOTAL_CONTEXT_CHARS = 2000


class WorkflowMixin:
    """
    Mixin adding workflow capabilities to llm-assistant session.
    
    Provides:
    - Workflow loading and execution
    - /workflow slash commands
    - System prompt injection with workflow context
    - Interrupt handling and state preservation
    
    Attributes expected from base session:
    - self.console: Rich Console for output
    - self.model: LLM model instance
    - self.execute_command(): Method for command execution
    - self.prompt_async(): Method for user prompts
    """
    
    # Workflow state
    current_workflow: Optional[Any] = None
    workflow_app: Optional[Any] = None
    workflow_executor: Optional[Any] = None
    workflow_running: bool = False
    workflow_waiting_for_input: bool = False

    # Internal state
    _workflow_ctx: dict = None
    _workflow_progress: Optional[Any] = None
    _interrupt_requested: bool = False
    _current_process: Optional[Any] = None
    _current_process_pgid: Optional[int] = None
    _original_sigint_handler: Optional[Any] = None
    _workflow_llm_client: Optional[Any] = None  # For timeout coordination
    _workflow_audit_logger: Optional[Any] = None  # For execution audit logging
    
    def _workflow_init(self) -> None:
        """Initialize workflow state. Call from session __init__."""
        self.current_workflow = None
        self.workflow_app = None
        self.workflow_executor = None
        self.workflow_running = False
        self.workflow_waiting_for_input = False
        self._workflow_ctx = {}
        self._workflow_progress = None
        self._interrupt_requested = False
        self._workflow_db_path = None
        self._workflow_audit_logger = None

    def _get_workflow_db_path(self, workflow_name: str) -> Path:
        """Get the database path for workflow persistence.

        Creates the workflows directory if it doesn't exist.

        Args:
            workflow_name: Name of the workflow (sanitized for filename)

        Returns:
            Path to the SQLite database for this workflow
        """
        # Sanitize workflow name for filename
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', workflow_name)[:50]

        # Use llm-assistant config directory
        config_dir = Path.home() / ".config" / "llm-assistant" / "workflows"
        config_dir.mkdir(parents=True, exist_ok=True)

        return config_dir / f"{safe_name}.db"

    def _create_workflow_action_registry(self, llm_client: Optional[Any] = None) -> "ActionRegistry":
        """Create action registry with default + custom llm-assistant actions.

        Registers:
        - All default burr_workflow actions (shell, http, state/*, control/*, etc.)
        - human/input: Interactive user prompts via exec_context.prompt_user()
        - human/decide: Constrained decisions (confirm/choice) via exec_context.prompt_user()
        - report/add: Pentest finding management via ReportMixin

        Args:
            llm_client: Optional LLM client for llm/* actions

        Returns:
            ActionRegistry with all registered actions
        """
        from burr_workflow.actions import get_default_registry
        from .actions import HumanInputAction, HumanDecideAction, ReportAddAction

        # Start with default registry
        registry = get_default_registry(llm_client=llm_client)

        # Register llm-assistant specific actions (override burr_workflow defaults)
        registry.register("human/input", HumanInputAction)
        registry.register("human/decide", HumanDecideAction)
        registry.register("report/add", ReportAddAction)

        return registry

    async def _workflow_load(self, path: str) -> bool:
        """Load a workflow from YAML file.
        
        Args:
            path: Path to workflow YAML file
            
        Returns:
            True if loaded successfully
        """
        try:
            from burr_workflow import (
                WorkflowCompiler,
                WorkflowExecutor,
                validate_workflow_yaml,
            )
            from burr_workflow.persistence import FileAuditLogger
            from .context import AssistantExecutionContext
            from .llm_client import AssistantLLMClient

            # Resolve path
            workflow_path = Path(path).expanduser().resolve()
            if not workflow_path.exists():
                self.console.print(f"[red]Workflow file not found: {path}[/]")
                return False

            # Read YAML content for validation with source tracking
            with open(workflow_path) as f:
                yaml_content = f.read()

            # Validate with source location tracking (line:column in error messages)
            result = validate_workflow_yaml(yaml_content)
            if not result.valid:
                self.console.print("[red]Workflow validation failed:[/]")
                for error in result.errors:
                    self.console.print(f"  [{error.code}] {error.message}")
                    if error.location:
                        self.console.print(f"         at {error.location}")
                return False
            
            for warning in result.warnings:
                self.console.print(f"[yellow]Warning: [{warning.code}] {warning.message}[/]")

            # Parse YAML into dict for compilation (validation already done)
            try:
                from ruamel.yaml import YAML
                from io import StringIO
                yaml = YAML(typ="safe")
                workflow_dict = yaml.load(StringIO(yaml_content))
            except ImportError:
                import yaml as pyyaml
                workflow_dict = pyyaml.safe_load(yaml_content)

            # Create execution context with session reference for actions like report/add
            # Use execute_command_async for full parameter support (timeout, env, cwd, etc.)
            exec_context = AssistantExecutionContext(
                console=self.console,
                execute_fn=getattr(self, 'execute_command_async', None),
                prompt_fn=getattr(self, 'prompt_async', None),
                working_dir=os.getcwd(),
                on_process_start=self._workflow_register_process,
                on_process_end=self._workflow_unregister_process,
                session=self,  # For ReportAddAction and other session-aware actions
            )
            
            # Create LLM client (store for timeout coordination)
            llm_client = AssistantLLMClient(
                model=self.model,
                conversation=getattr(self, 'conversation', None),
            )
            self._workflow_llm_client = llm_client
            
            # Create action registry with custom actions
            action_registry = self._create_workflow_action_registry(llm_client)

            # Compile workflow with persistence
            compiler = WorkflowCompiler(
                action_registry=action_registry,
                exec_context=exec_context,
                llm_client=llm_client,
            )

            # Get workflow name for persistence
            workflow_name = workflow_dict.get("name", workflow_path.stem)
            self._workflow_db_path = self._get_workflow_db_path(workflow_name)

            self.workflow_app = compiler.compile(
                workflow_dict,
                db_path=self._workflow_db_path,
            )
            self.current_workflow = workflow_dict
            self._workflow_ctx = {
                "name": workflow_name,
                "steps": {},
                "db_path": str(self._workflow_db_path),
            }

            # Create audit logger for execution logging
            # Logs stored in ~/.config/llm-assistant/workflow-logs/
            audit_log_dir = Path.home() / ".config" / "llm-assistant" / "workflow-logs"
            audit_log_dir.mkdir(parents=True, exist_ok=True)
            self._workflow_audit_logger = FileAuditLogger(log_dir=audit_log_dir)

            # Create executor with audit logging
            self.workflow_executor = WorkflowExecutor(
                exec_context=exec_context,
                audit_logger=self._workflow_audit_logger,
                on_progress=self._workflow_on_progress,
                on_step=self._workflow_on_step,
            )
            
            self.console.print(
                f"[green]✓ Loaded workflow: {workflow_dict.get('name', path)}[/]"
            )
            return True
            
        except Exception as e:
            self.console.print(f"[red]Failed to load workflow: {e}[/]")
            return False
    
    async def _workflow_run(
        self,
        inputs: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> bool:
        """Run the loaded workflow.
        
        Args:
            inputs: Input values for the workflow
            timeout: Execution timeout in seconds
            
        Returns:
            True if workflow completed successfully
        """
        if not self.workflow_app or not self.workflow_executor:
            self.console.print("[red]No workflow loaded. Use /workflow load <file>[/]")
            return False
        
        if self.workflow_running:
            self.console.print("[yellow]Workflow already running[/]")
            return False
        
        self.workflow_running = True
        self._interrupt_requested = False

        # Set up interrupt handler
        self._setup_workflow_signal_handler()

        # Coordinate LLM timeout with workflow timeout
        # Use executor's default_timeout if not specified, to ensure coordination
        effective_timeout = timeout or self.workflow_executor.default_timeout
        if self._workflow_llm_client:
            import time
            self._workflow_llm_client.set_workflow_timeout(effective_timeout, time.monotonic())

        try:
            self.console.print(
                f"[bold]▶ Running workflow: {self._workflow_ctx.get('name', 'Unknown')}[/]"
            )

            result = await self.workflow_executor.run(
                self.workflow_app,
                inputs=inputs,
                timeout=timeout,
            )
            
            # Update context
            self._workflow_ctx["steps"] = result.final_state.get("steps", {})
            self._workflow_progress = result.progress
            
            if result.suspended:
                self.workflow_waiting_for_input = True
                self.console.print(
                    f"\n[yellow]⏸ Workflow suspended: {result.suspension.prompt}[/]"
                )
                if result.suspension.options:
                    for i, opt in enumerate(result.suspension.options, 1):
                        self.console.print(f"    {i}. {opt}")
                return True
            
            if result.success:
                self.console.print(
                    f"\n[green]✓ Workflow completed successfully "
                    f"({result.progress.steps_completed}/{result.progress.steps_total} steps)[/]"
                )
            elif result.failed:
                self.console.print(
                    f"\n[red]✗ Workflow failed: {result.error}[/]"
                )
            elif result.status.value == "interrupted":
                self.console.print(
                    f"\n[yellow]⚠ Workflow interrupted. Resume with /workflow resume[/]"
                )
            elif result.status.value == "timeout":
                self.console.print(
                    f"\n[red]✗ Workflow timed out[/]"
                )
            
            return result.success
            
        finally:
            self.workflow_running = False
            self._restore_signal_handler()
            # Clear LLM timeout coordination
            if self._workflow_llm_client:
                self._workflow_llm_client.clear_workflow_timeout()
    
    async def _workflow_resume(self, input_value: Any = None) -> bool:
        """Resume a suspended workflow with user input.
        
        Args:
            input_value: The user-provided input
            
        Returns:
            True if resumed and completed successfully
        """
        if not self.workflow_waiting_for_input:
            self.console.print("[yellow]No workflow waiting for input[/]")
            return False
        
        self.workflow_waiting_for_input = False
        
        try:
            result = await self.workflow_executor.resume(
                input_value=input_value,
            )
            
            # Update context
            self._workflow_ctx["steps"] = result.final_state.get("steps", {})
            self._workflow_progress = result.progress
            
            return result.success
            
        except Exception as e:
            self.console.print(f"[red]Failed to resume workflow: {e}[/]")
            return False
    
    def _workflow_status(self) -> None:
        """Display current workflow status."""
        if not self.current_workflow:
            self.console.print("[dim]No workflow loaded[/]")
            return
        
        name = self._workflow_ctx.get("name", "Unknown")
        self.console.print(f"[bold]Workflow:[/] {name}")
        
        if self._workflow_progress:
            progress = self._workflow_progress
            self.console.print(
                f"[bold]Status:[/] {progress.status.value} "
                f"({progress.steps_completed}/{progress.steps_total} steps)"
            )
            if progress.current_step:
                self.console.print(f"[bold]Current step:[/] {progress.current_step}")
        
        if self.workflow_running:
            self.console.print("[green]● Running[/]")
        elif self.workflow_waiting_for_input:
            self.console.print("[yellow]⏸ Waiting for input[/]")
        else:
            self.console.print("[dim]○ Idle[/]")
        
        # Show recent step results
        steps = self._workflow_ctx.get("steps", {})
        if steps:
            self.console.print("\n[bold]Recent steps:[/]")
            for step_id, step_data in list(steps.items())[-5:]:
                outcome = step_data.get("outcome", "?")
                icon = "✓" if outcome == "success" else "✗" if outcome == "failure" else "○"
                color = "green" if outcome == "success" else "red" if outcome == "failure" else "dim"
                self.console.print(f"  [{color}]{icon} {step_id}[/]: {outcome}")
    
    def _workflow_stop(self) -> None:
        """Stop the running workflow."""
        if not self.workflow_running:
            self.console.print("[yellow]No workflow running[/]")
            return
        
        self._interrupt_requested = True
        if self.workflow_executor:
            self.workflow_executor.interrupt()
        
        self.console.print("[yellow]Stopping workflow...[/]")
    
    def _workflow_clear(self) -> None:
        """Clear the current workflow."""
        # Close audit logger if active
        if self._workflow_audit_logger is not None:
            try:
                # FileAuditLogger uses sync file I/O internally
                # Directly close the file handle for sync cleanup
                if hasattr(self._workflow_audit_logger, '_jsonl_file'):
                    if self._workflow_audit_logger._jsonl_file is not None:
                        self._workflow_audit_logger._jsonl_file.close()
                        self._workflow_audit_logger._jsonl_file = None
            except Exception:
                pass  # Best-effort cleanup
            self._workflow_audit_logger = None

        self.current_workflow = None
        self.workflow_app = None
        self.workflow_executor = None
        self.workflow_running = False
        self.workflow_waiting_for_input = False
        self._workflow_ctx = {}
        self._workflow_progress = None
        self.console.print("[dim]Workflow cleared[/]")
    
    def _get_workflow_context(self) -> str:
        """Generate dynamic workflow context for system prompt.
        
        CRITICAL: Aggressive summarization to prevent context window bloat.
        Full outputs are accessible via read_file or dedicated analyze steps.
        
        Returns:
            Workflow context XML block
        """
        if not self.current_workflow:
            return ""
        
        name = self._workflow_ctx.get("name", "Unknown")
        
        # Get progress info
        current_step = "N/A"
        progress_str = "0/0"
        if self._workflow_progress:
            current_step = self._workflow_progress.current_step or "N/A"
            progress_str = (
                f"{self._workflow_progress.steps_completed}/"
                f"{self._workflow_progress.steps_total}"
            )
        
        # Format recent outputs
        recent_outputs = self._format_recent_step_outputs()
        
        return f"""
<workflow>
  <name>{name}</name>
  <current_step>{current_step}</current_step>
  <progress>{progress_str}</progress>
  <status>{"running" if self.workflow_running else "suspended" if self.workflow_waiting_for_input else "idle"}</status>
  <recent_outputs>
    {recent_outputs}
  </recent_outputs>
</workflow>
"""
    
    def _format_recent_step_outputs(self) -> str:
        """Format recent step outputs for system prompt injection.
        
        AGGRESSIVE SUMMARIZATION RULES:
        1. Only show last 3 completed steps
        2. Max 500 chars per step output
        3. For file-based outputs, show path only (not content)
        4. Show outcome and key metrics, not raw data
        5. Total output capped at 2000 chars
        """
        steps = self._workflow_ctx.get("steps", {})
        if not steps:
            return "(no completed steps)"
        
        summaries = []
        recent_steps = list(steps.items())[-3:]  # Last 3 steps
        total_chars = 0
        
        for step_id, step_data in recent_steps:
            outcome = step_data.get("outcome", "unknown")
            outputs = step_data.get("outputs", {})
            
            # Build summary based on output type
            if "file" in outputs:
                # File-based output: show path, not content
                summary = f"[{step_id}] {outcome} → file: {outputs['file']}"
                if "size_bytes" in outputs:
                    summary += f" ({outputs['size_bytes']} bytes)"
            elif "stdout" in outputs:
                # Memory output: truncate aggressively
                stdout = outputs["stdout"]
                if len(stdout) > MAX_OUTPUT_SUMMARY_CHARS:
                    lines = stdout.strip().split('\n')
                    if len(lines) > 6:
                        preview = '\n'.join(
                            lines[:3] + 
                            ['...', f'({len(lines)} lines total)', '...'] + 
                            lines[-2:]
                        )
                    else:
                        preview = stdout[:MAX_OUTPUT_SUMMARY_CHARS] + "..."
                    summary = f"[{step_id}] {outcome}:\n{preview}"
                else:
                    summary = f"[{step_id}] {outcome}: {stdout}"
            elif "results" in outputs:
                # Loop aggregation: show counts only
                results = outputs["results"]
                summary = (
                    f"[{step_id}] {outcome}: {len(results)} results, "
                    f"{outputs.get('succeeded', 0)} succeeded, "
                    f"{outputs.get('failed', 0)} failed"
                )
            else:
                # Generic: show keys only
                keys = list(outputs.keys())[:5]
                suffix = '...' if len(outputs) > 5 else ''
                summary = f"[{step_id}] {outcome}: {{{', '.join(keys)}{suffix}}}"
            
            # Enforce per-step limit
            if len(summary) > MAX_OUTPUT_SUMMARY_CHARS:
                summary = summary[:MAX_OUTPUT_SUMMARY_CHARS] + "..."
            
            # Enforce total limit
            if total_chars + len(summary) > MAX_TOTAL_CONTEXT_CHARS:
                remaining = len(recent_steps) - len(summaries)
                summaries.append(
                    f"... ({remaining} more steps, use /workflow status for details)"
                )
                break
            
            summaries.append(summary)
            total_chars += len(summary)
        
        return '\n    '.join(summaries)
    
    async def _handle_workflow_command(self, args: str) -> bool:
        """Handle /workflow slash commands.
        
        Args:
            args: Command arguments after "/workflow "
            
        Returns:
            True if command was handled
        """
        parts = args.strip().split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""
        subargs = parts[1] if len(parts) > 1 else ""
        
        if subcmd == "load":
            if not subargs:
                self.console.print("[red]Usage: /workflow load <file.yaml>[/]")
                return True
            await self._workflow_load(subargs)
            return True
        
        elif subcmd == "run":
            # Parse inputs from subargs if provided
            inputs = None
            if subargs:
                try:
                    import json
                    inputs = json.loads(subargs)
                except json.JSONDecodeError:
                    # Try key=value format
                    inputs = {}
                    for pair in subargs.split():
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            inputs[k] = v
            await self._workflow_run(inputs=inputs)
            return True
        
        elif subcmd == "stop":
            self._workflow_stop()
            return True
        
        elif subcmd == "status":
            self._workflow_status()
            return True
        
        elif subcmd == "resume":
            if subargs:
                await self._workflow_resume(subargs)
            else:
                self.console.print("[yellow]Provide input value: /workflow resume <value>[/]")
            return True
        
        elif subcmd == "clear":
            self._workflow_clear()
            return True
        
        elif subcmd == "help" or not subcmd:
            self.console.print("""[bold]Workflow Commands:[/]
  /workflow load <file.yaml>  - Load a workflow
  /workflow run [inputs]      - Run the loaded workflow
  /workflow status            - Show workflow status
  /workflow stop              - Stop running workflow
  /workflow resume <value>    - Resume with user input
  /workflow clear             - Unload workflow""")
            return True
        
        else:
            self.console.print(f"[red]Unknown workflow command: {subcmd}[/]")
            return True
    
    def _workflow_register_process(self, process) -> None:
        """Register a running subprocess for interrupt handling."""
        self._current_process = process
        try:
            self._current_process_pgid = os.getpgid(process.pid)
        except (OSError, ProcessLookupError):
            self._current_process_pgid = None
    
    def _workflow_unregister_process(self) -> None:
        """Unregister subprocess after completion."""
        self._current_process = None
        self._current_process_pgid = None
    
    def _setup_workflow_signal_handler(self) -> None:
        """Set up signal handler for workflow interruption."""
        try:
            self._original_sigint_handler = signal.signal(
                signal.SIGINT,
                self._workflow_handle_interrupt
            )
        except (ValueError, OSError):
            pass  # Not in main thread
    
    def _restore_signal_handler(self) -> None:
        """Restore original signal handler."""
        if self._original_sigint_handler:
            try:
                signal.signal(signal.SIGINT, self._original_sigint_handler)
            except (ValueError, OSError):
                pass
            self._original_sigint_handler = None
    
    def _workflow_handle_interrupt(self, signum, frame) -> None:
        """Handle Ctrl+C during workflow execution."""
        if self.workflow_running:
            self._interrupt_requested = True
            
            # Terminate running process immediately
            if self._current_process_pgid:
                self.console.print(
                    "\n[yellow]⚠ Interrupt received, terminating current process...[/]"
                )
                try:
                    os.killpg(self._current_process_pgid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
            else:
                self.console.print(
                    "\n[yellow]⚠ Interrupt received, stopping after current step...[/]"
                )
            
            if self.workflow_executor:
                self.workflow_executor.interrupt()
            return
        
        # Not in workflow - raise to default handler
        raise KeyboardInterrupt
    
    def _workflow_on_progress(self, progress) -> None:
        """Callback for progress updates."""
        self._workflow_progress = progress
    
    def _workflow_on_step(
        self, 
        step_id: str, 
        status: str, 
        outputs: Optional[dict]
    ) -> None:
        """Callback for step transitions."""
        icon = "✓" if status == "completed" else "▶" if status == "running" else "○"
        color = "green" if status == "completed" else "cyan" if status == "running" else "dim"
        self.console.print(f"[{color}]{icon} {step_id}[/]")
