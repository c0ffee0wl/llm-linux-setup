"""
Integration protocols for the workflow engine.

These protocols define the interfaces that integrations must
implement to connect burr_workflow with their environment.
This allows burr_workflow to remain standalone while enabling
rich integrations with different systems.
"""

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class ExecutionContext(Protocol):
    """Protocol for execution environment integration.

    Provides the bridge between workflow actions and the host
    environment (shell execution, user interaction, logging).

    Implementations:
        - LLMAssistantContext: llm-assistant integration
        - CLIContext: Standalone CLI usage
        - APIContext: Web API usage
    """

    async def execute_shell(
        self,
        command: str,
        timeout: int,
        env: dict[str, str],
        *,
        cwd: Optional[str] = None,
        capture: bool = True,
    ) -> tuple[bool, str, str]:
        """Execute shell command.

        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            env: Environment variables to set
            cwd: Working directory (optional)
            capture: Whether to capture output

        Returns:
            Tuple of (success, stdout, stderr)
        """
        ...

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
        ...

    async def prompt_user(
        self,
        prompt: str,
        *,
        options: Optional[list[str]] = None,
        default: Optional[str] = None,
    ) -> str:
        """Prompt user for input.

        Args:
            prompt: The prompt message
            options: Optional list of choices
            default: Default value if user enters nothing

        Returns:
            User's response
        """
        ...

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
        ...

    def log(
        self,
        level: str,
        message: str,
        *,
        step_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Log a message.

        Args:
            level: Log level (debug, info, warning, error)
            message: Log message
            step_id: Optional step ID for context
            **kwargs: Additional structured data
        """
        ...


@runtime_checkable
class OutputHandler(Protocol):
    """Protocol for output display/capture.

    Handles displaying workflow progress and results to the user.
    Separated from ExecutionContext to allow different output
    strategies (Rich console, plain text, JSON, etc.).
    """

    def write(
        self,
        text: str,
        *,
        style: Optional[str] = None,
        end: str = "\n",
    ) -> None:
        """Write text output.

        Args:
            text: Text to output
            style: Optional style (implementation-dependent)
            end: Line ending
        """
        ...

    def progress(
        self,
        current: int,
        total: int,
        message: str,
        *,
        step_id: Optional[str] = None,
    ) -> None:
        """Display progress indicator.

        Args:
            current: Current step number
            total: Total number of steps
            message: Progress message
            step_id: Optional step ID
        """
        ...

    def step_start(
        self,
        step_id: str,
        step_name: Optional[str],
        step_type: str,
    ) -> None:
        """Called when a step starts.

        Args:
            step_id: Step identifier
            step_name: Human-readable step name
            step_type: Type of step (run, uses, etc.)
        """
        ...

    def step_end(
        self,
        step_id: str,
        outcome: str,
        duration_ms: float,
        *,
        error: Optional[str] = None,
    ) -> None:
        """Called when a step ends.

        Args:
            step_id: Step identifier
            outcome: Step outcome (success, failure, skipped)
            duration_ms: Duration in milliseconds
            error: Error message if failed
        """
        ...

    def error(
        self,
        message: str,
        *,
        step_id: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        """Display error message.

        Args:
            message: Error message
            step_id: Optional step ID
            details: Optional detailed error info
        """
        ...


@runtime_checkable
class ActionProvider(Protocol):
    """Protocol for custom action registration.

    Allows extending the workflow engine with custom action
    types beyond the built-in ones.
    """

    def get_action(self, action_type: str) -> Optional[type]:
        """Get action class by type name.

        Args:
            action_type: Action type (e.g., 'llm/extract', 'custom/action')

        Returns:
            Action class or None if not found
        """
        ...

    def register_action(self, action_type: str, action_class: type) -> None:
        """Register custom action type.

        Args:
            action_type: Action type name
            action_class: Action class implementing BaseAction
        """
        ...

    def list_actions(self) -> list[str]:
        """List all registered action types.

        Returns:
            List of action type names
        """
        ...


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM integration.

    This allows burr_workflow to have generic AI capabilities
    (llm/extract, llm/decide, etc.) without depending on a
    specific LLM provider.

    Implementations:
        - AssistantLLMClient: Uses llm-assistant's Session.model
        - OpenAILLMClient: Direct OpenAI SDK usage
        - OllamaLLMClient: Local ollama backend
    """

    async def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate text completion.

        Args:
            prompt: The user prompt
            system: Optional system prompt
            temperature: Sampling temperature (0.0 = deterministic)
            max_tokens: Maximum tokens to generate

        Returns:
            Generated text response
        """
        ...

    async def complete_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system: Optional[str] = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Generate structured JSON output matching a schema.

        Uses JSON mode or function calling if available,
        with retry logic for schema validation failures.

        Args:
            prompt: The user prompt describing what to extract
            schema: JSON Schema defining expected output structure
            system: Optional system prompt
            max_retries: Retry count for schema validation failures

        Returns:
            Parsed and validated JSON object

        Raises:
            LLMSchemaValidationError: If output doesn't match schema
        """
        ...

    async def complete_choice(
        self,
        prompt: str,
        choices: list[str],
        *,
        system: Optional[str] = None,
    ) -> str:
        """Select from predefined choices.

        Optimized for decision-making where output must be
        one of the choices.

        Args:
            prompt: The decision prompt
            choices: Valid choices to select from
            system: Optional system prompt

        Returns:
            Selected choice (guaranteed to be in choices list)

        Raises:
            LLMChoiceError: If LLM returns invalid choice
        """
        ...


@runtime_checkable
class PersistenceBackend(Protocol):
    """Protocol for workflow state persistence.

    Enables save/restore of workflow state for:
    - Resume after interruption
    - Crash recovery
    - Workflow history/audit
    """

    async def save_state(
        self,
        workflow_id: str,
        app_id: str,
        state: dict[str, Any],
        position: str,
        sequence_id: int,
    ) -> None:
        """Save workflow state.

        Args:
            workflow_id: Workflow definition ID
            app_id: Application/execution instance ID
            state: Current state dictionary
            position: Current node/action name
            sequence_id: Step sequence number
        """
        ...

    async def load_state(
        self,
        workflow_id: str,
        app_id: str,
    ) -> Optional[dict[str, Any]]:
        """Load workflow state.

        Args:
            workflow_id: Workflow definition ID
            app_id: Application/execution instance ID

        Returns:
            State dictionary or None if not found
        """
        ...

    async def list_executions(
        self,
        workflow_id: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List recent executions of a workflow.

        Args:
            workflow_id: Workflow definition ID
            limit: Maximum number of results

        Returns:
            List of execution metadata
        """
        ...


@runtime_checkable
class ReportBackend(Protocol):
    """Protocol for finding storage backends.

    This allows burr_workflow to have report/add action without
    depending on a specific finding storage implementation.

    Implementations:
        - ReportMixin: llm-assistant's finding management
        - FileReportBackend: Standalone file-based storage
    """

    @property
    def findings_project(self) -> Optional[str]:
        """Current project name, or None if not initialized.

        Returns:
            Project name string or None
        """
        ...

    async def add_finding(
        self,
        note: str,
        *,
        severity_override: Optional[int] = None,
        context: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a finding with optional LLM analysis.

        Args:
            note: Quick note describing the vulnerability
            severity_override: Override LLM-suggested severity (1-9 OWASP scale)
            context: Optional terminal/execution context for analysis

        Returns:
            Dict with:
                - finding_id: Unique finding identifier (e.g., "F001")
                - title: Finding title (LLM-generated or from note)
                - severity: Severity 1-9 (9 = critical)
                - success: Whether finding was added successfully
                - error: Error message if not successful
        """
        ...


class LLMSchemaValidationError(Exception):
    """LLM output doesn't match expected schema."""
    pass


class LLMChoiceError(Exception):
    """LLM returned invalid choice."""
    pass
