"""
Pydantic models for workflow YAML validation.

These models provide compile-time validation of workflow definitions,
catching errors before runtime execution.
"""

from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class ShellSafetyMode(str, Enum):
    """Shell safety enforcement mode."""
    STRICT = "strict"  # Unquoted variables are compile-time errors
    AUTO_QUOTE = "auto_quote"  # Automatically add shell_quote


class CaptureMode(str, Enum):
    """Output capture mode for shell commands."""
    MEMORY = "memory"  # Store in state (default, for small outputs)
    FILE = "file"  # Write to file (for large outputs)
    NONE = "none"  # Discard output


class ResultStorageMode(str, Enum):
    """How to store loop results."""
    MEMORY = "memory"  # In-state list (default, limited by max_results)
    FILE = "file"  # JSONL file (scalable)
    NONE = "none"  # Don't store (fire-and-forget)


class StepOutcomeEnum(str, Enum):
    """Possible step outcomes."""
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"


# ==============================================================================
# Input Definitions
# ==============================================================================

class InputDefinition(BaseModel):
    """Definition for a workflow input parameter."""
    description: Optional[str] = None
    type: Optional[str] = Field(
        default="string",
        description="Input type: string, number, boolean, array, object",
    )
    default: Optional[Any] = None
    required: bool = Field(
        default=True,
        description="Whether this input is required",
    )
    enum: Optional[list[Any]] = Field(
        default=None,
        description="Allowed values (if restricted)",
    )
    pattern: Optional[str] = Field(
        default=None,
        description="Regex pattern for validation (strings only)",
    )
    secret: bool = Field(
        default=False,
        description="Whether this input contains sensitive data",
    )


# ==============================================================================
# Retry Configuration
# ==============================================================================

class RetryConfig(BaseModel):
    """Configuration for step retry behavior.

    Example:
        retry:
          max_attempts: 3
          delay: 2.0
          backoff: 2.0
          max_delay: 60.0
          retry_on:
            - TimeoutError
            - ConnectionError
    """
    max_attempts: int = Field(default=3, ge=1, le=10)
    delay: float = Field(
        default=1.0,
        ge=0,
        description="Initial delay between retries in seconds",
    )
    backoff: float = Field(
        default=2.0,
        ge=1.0,
        description="Exponential backoff multiplier",
    )
    max_delay: float = Field(
        default=60.0,
        ge=0,
        description="Maximum delay between retries",
    )
    jitter: bool = Field(
        default=True,
        description="Add randomness to backoff (0.5-1.5x) to prevent thundering herd",
    )
    retry_on: Optional[list[str]] = Field(
        default=None,
        description="Error types to retry on (None = default network errors)",
    )


# ==============================================================================
# Guardrail Configuration
# ==============================================================================

class GuardrailConfig(BaseModel):
    """Configuration for output validation and steering."""
    type: Literal["regex", "json_schema", "llm_judge"]
    pattern: Optional[str] = Field(
        default=None,
        description="Regex pattern (for type=regex)",
    )
    schema_: Optional[dict[str, Any]] = Field(
        default=None,
        alias="schema",
        description="JSON Schema (for type=json_schema)",
    )
    prompt: Optional[str] = Field(
        default=None,
        description="LLM judge prompt (for type=llm_judge)",
    )
    on_fail: Literal["error", "retry", "continue"] = "error"
    max_retries: int = Field(default=3, ge=1)


# ==============================================================================
# Action-Specific Configurations
# ==============================================================================

class HTTPRequestConfig(BaseModel):
    """Configuration for http/request action."""
    url: str
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"] = "GET"
    headers: Optional[dict[str, str]] = None
    secret_headers: Optional[dict[str, str]] = Field(
        default=None,
        description="Headers read from secrets directory (never logged)",
    )
    body: Optional[Any] = None
    json_body: Optional[dict[str, Any]] = Field(
        default=None,
        alias="json",
        description="JSON body (sets Content-Type automatically)",
    )
    timeout: int = Field(default=30, ge=1, le=3600)
    follow_redirects: bool = True
    verify_ssl: bool = True


class LLMActionConfig(BaseModel):
    """Configuration for llm/* actions."""
    content: Optional[str] = Field(
        default=None,
        description="Content to analyze/extract from",
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Custom prompt (overrides default)",
    )
    schema_: Optional[dict[str, Any]] = Field(
        default=None,
        alias="schema",
        description="JSON schema for structured output (llm/extract)",
    )
    choices: Optional[list[str]] = Field(
        default=None,
        description="Decision options (llm/decide)",
    )
    model: Optional[str] = Field(
        default=None,
        description="Model override (default: client's configured model)",
    )
    temperature: Optional[float] = Field(
        default=None,
        ge=0,
        le=2,
        description="Sampling temperature (default varies by action type)",
    )
    max_tokens: Optional[int] = Field(default=None, ge=1)
    chunk_size: Optional[int] = Field(
        default=None,
        description="Chunk size for large content processing",
    )
    chunk_overlap: int = Field(
        default=100,
        description="Token overlap between chunks",
    )


class HumanInputConfig(BaseModel):
    """Configuration for human/input action."""
    prompt: str
    options: Optional[list[str]] = Field(
        default=None,
        description="Predefined choices (shows as buttons/menu)",
    )
    default: Optional[str] = None
    timeout: Optional[int] = Field(
        default=None,
        description="Timeout in seconds (None = wait indefinitely)",
    )
    multiline: bool = Field(
        default=False,
        description="Allow multiline input",
    )


class ScriptConfig(BaseModel):
    """Configuration for script/* action."""
    path: Optional[str] = Field(
        default=None,
        description="Path to script file",
    )
    inline: Optional[str] = Field(
        default=None,
        description="Inline script content",
    )
    language: Optional[Literal["python", "bash", "powershell"]] = Field(
        default=None,
        description="Script language (auto-detected from path extension)",
    )
    args: Optional[list[str]] = None
    env: Optional[dict[str, str]] = None

    @model_validator(mode="after")
    def validate_source(self) -> "ScriptConfig":
        if not self.path and not self.inline:
            raise ValueError("Either 'path' or 'inline' must be specified")
        if self.path and self.inline:
            raise ValueError("Cannot specify both 'path' and 'inline'")
        return self


class StateSetConfig(BaseModel):
    """Configuration for state/set action."""
    variables: dict[str, Any]


# ==============================================================================
# Step Definition
# ==============================================================================

class StepDefinition(BaseModel):
    """Definition for a single workflow step."""

    # Identity
    name: Optional[str] = Field(
        default=None,
        description="Human-readable step name",
    )
    id: Optional[str] = Field(
        default=None,
        description="Step ID for referencing outputs",
    )

    # Action (exactly one of these must be set)
    run: Optional[Union[str, list[str]]] = Field(
        default=None,
        description="Shell command (string or array)",
    )
    uses: Optional[str] = Field(
        default=None,
        description="Action type (e.g., 'llm/extract', 'http/request')",
    )

    # Action configuration
    with_: Optional[dict[str, Any]] = Field(
        default=None,
        alias="with",
        description="Action-specific configuration",
    )

    # Conditional execution
    if_: Optional[str] = Field(
        default=None,
        alias="if",
        description="Condition expression (step skipped if false)",
    )

    # Loop iteration
    loop: Optional[str] = Field(
        default=None,
        description="Expression returning list to iterate over",
    )
    break_if: Optional[str] = Field(
        default=None,
        description="Condition to break out of loop early",
    )
    max_iterations: int = Field(
        default=10000,
        ge=1,
        description="Maximum loop iterations (safety limit)",
    )
    max_results: int = Field(
        default=100,
        ge=0,
        description="Maximum results to keep in state (sliding window)",
    )
    max_errors: int = Field(
        default=50,
        ge=0,
        description="Maximum errors to accumulate before failing loop",
    )
    continue_on_error: bool = Field(
        default=False,
        description="Continue loop even if iteration fails",
    )
    aggregate_results: bool = Field(
        default=True,
        description="Whether to store iteration results",
    )
    result_storage: ResultStorageMode = Field(
        default=ResultStorageMode.MEMORY,
        description="Where to store loop results",
    )

    # Shell command options
    interactive: bool = Field(
        default=False,
        description="Run command interactively (no capture)",
    )
    capture_mode: CaptureMode = Field(
        default=CaptureMode.MEMORY,
        description="How to capture command output",
    )

    # Timeout and error handling
    timeout: int = Field(
        default=300,
        ge=1,
        le=86400,
        description="Timeout in seconds (default: 5 minutes)",
    )
    on_failure: Optional[str] = Field(
        default=None,
        description="Step ID to jump to on failure",
    )
    resume_from: Optional[str] = Field(
        default=None,
        description="Where to continue after error handler",
    )

    # Retry configuration
    retry: Optional[RetryConfig] = None

    # Output validation
    guardrails: Optional[list[GuardrailConfig]] = None

    # Idempotency marker
    idempotent: bool = Field(
        default=True,
        description="Whether step is safe to re-run on resume",
    )

    @model_validator(mode="after")
    def validate_action(self) -> "StepDefinition":
        """Ensure exactly one action type is specified."""
        action_count = sum([
            self.run is not None,
            self.uses is not None,
        ])
        if action_count == 0:
            raise ValueError("Step must have either 'run' or 'uses'")
        if action_count > 1:
            raise ValueError("Step cannot have both 'run' and 'uses'")
        return self

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate step ID format."""
        if v is None:
            return v

        import re
        from ..evaluator.security import validate_step_id
        validate_step_id(v)
        return v


# ==============================================================================
# Job Definition
# ==============================================================================

class JobDefinition(BaseModel):
    """Definition for a workflow job."""
    name: Optional[str] = None
    steps: list[StepDefinition]
    finally_: Optional[list[StepDefinition]] = Field(
        default=None,
        alias="finally",
        description="Cleanup steps (always run)",
    )


# ==============================================================================
# Workflow Definition
# ==============================================================================

class WorkflowDefinition(BaseModel):
    """Root workflow definition model."""

    # Identity
    name: str = Field(description="Workflow name")
    description: Optional[str] = None
    version: Optional[str] = Field(
        default=None,
        description="Workflow version (semver recommended, e.g., '1.0.0')",
    )
    author: Optional[str] = Field(
        default=None,
        description="Workflow author or maintainer",
    )
    schema_version: str = Field(
        default="1.0",
        description="Schema version for compatibility",
    )

    # Inputs and environment
    inputs: Optional[dict[str, Union[InputDefinition, Any]]] = Field(
        default=None,
        description="Input parameter definitions",
    )
    env: Optional[dict[str, str]] = Field(
        default=None,
        description="Environment variables",
    )

    # Jobs
    jobs: dict[str, JobDefinition] = Field(
        description="Job definitions (usually just 'main')",
    )

    # Lifecycle hooks
    finally_: Optional[list[StepDefinition]] = Field(
        default=None,
        alias="finally",
        description="Workflow-level cleanup",
    )
    on_complete: Optional[list[StepDefinition]] = Field(
        default=None,
        description="Steps to run on successful completion",
    )
    on_failure: Optional[list[StepDefinition]] = Field(
        default=None,
        description="Steps to run on workflow failure",
    )

    # Security settings
    shell_safety: ShellSafetyMode = Field(
        default=ShellSafetyMode.STRICT,
        description="Shell injection prevention mode",
    )
    workspace: Optional[str] = Field(
        default=None,
        description="Working directory for the workflow",
    )
    secrets_dir: Optional[str] = Field(
        default=None,
        description="Directory containing secret files",
    )

    # Execution settings
    default_timeout: int = Field(
        default=300,
        ge=1,
        description="Default step timeout in seconds",
    )
    max_parallel: int = Field(
        default=1,
        ge=1,
        description="Maximum parallel step execution (future)",
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        """Validate schema version is supported."""
        from ..core.types import SUPPORTED_SCHEMA_VERSIONS, DEPRECATED_VERSIONS
        if v not in SUPPORTED_SCHEMA_VERSIONS and v not in DEPRECATED_VERSIONS:
            raise ValueError(
                f"Unsupported schema version '{v}'. "
                f"Supported: {SUPPORTED_SCHEMA_VERSIONS}"
            )
        return v

    @model_validator(mode="after")
    def validate_jobs(self) -> "WorkflowDefinition":
        """Ensure at least one job exists."""
        if not self.jobs:
            raise ValueError("Workflow must have at least one job")
        if "main" not in self.jobs:
            raise ValueError("Workflow must have a 'main' job")
        return self
