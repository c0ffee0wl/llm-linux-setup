"""
Pydantic models for workflow YAML validation.

These models provide compile-time validation of workflow definitions,
catching errors before runtime execution.
"""

from enum import Enum
from typing import Any, Literal

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


class CaptureStderrMode(str, Enum):
    """How to handle stderr for shell commands."""
    MERGE = "merge"       # Merge stderr into stdout
    SEPARATE = "separate"  # Keep stdout and stderr separate (default)
    DISCARD = "discard"   # Discard stderr entirely


class ChunkingStrategy(str, Enum):
    """Text splitting strategy for large content."""
    LINE_AWARE = "line_aware"      # Split on line boundaries
    SLIDING_WINDOW = "sliding_window"  # Overlapping character windows


class AggregationStrategy(str, Enum):
    """Result aggregation strategy for chunked processing."""
    MERGE_STRUCTURED = "merge_structured"  # Deep merge JSON objects/arrays
    CONCATENATE = "concatenate"            # Join text with separator


class ChunkingConfig(BaseModel):
    """Configuration for chunking large content.

    Example:
        chunking:
          strategy: line_aware
          max_chars: 40000
          overlap: 100
    """
    strategy: ChunkingStrategy = Field(
        default=ChunkingStrategy.SLIDING_WINDOW,
        description="Splitting strategy",
    )
    max_chars: int = Field(
        default=40000,
        ge=1000,
        le=500000,
        description="Maximum characters per chunk",
    )
    overlap: int = Field(
        default=500,
        ge=0,
        le=5000,
        description="Overlap amount (lines for line_aware, chars for sliding_window)",
    )


class AggregationConfig(BaseModel):
    """Configuration for aggregating chunked results.

    Example:
        aggregation:
          strategy: merge_structured
          deduplicate_arrays: true
    """
    strategy: AggregationStrategy = Field(
        default=AggregationStrategy.CONCATENATE,
        description="Aggregation strategy",
    )
    separator: str = Field(
        default="\n\n",
        description="Separator for concatenate strategy",
    )
    deduplicate_arrays: bool = Field(
        default=True,
        description="Remove duplicates when merging arrays",
    )


# ==============================================================================
# LLM Configuration
# ==============================================================================

class LLMActionDefaultsConfig(BaseModel):
    """Per-action-type LLM defaults.

    Used within LLMDefaultsConfig to specify defaults for specific
    action types (extract, decide, generate, instruct).
    """
    model: str | None = Field(
        default=None,
        description="Model override for this action type",
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Temperature override for this action type",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Max tokens override for this action type",
    )


class LLMDefaultsConfig(BaseModel):
    """Workflow-level LLM configuration.

    Provides centralized defaults for all LLM actions. Step-level
    configuration in `with:` always takes precedence.

    Example:
        llm:
          model: gpt-4
          temperature: 0.7
          max_tokens: 2000
          extract:
            temperature: 0.3    # Lower for structured output
          decide:
            temperature: 0.0    # Deterministic
    """
    # Global defaults for all LLM actions
    model: str | None = Field(
        default=None,
        description="Default model for all LLM actions",
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Default temperature for all LLM actions",
    )
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Default max tokens for all LLM actions",
    )

    # Per-action-type overrides
    extract: LLMActionDefaultsConfig | None = Field(
        default=None,
        description="Defaults for llm/extract actions (default temp: 0.3)",
    )
    decide: LLMActionDefaultsConfig | None = Field(
        default=None,
        description="Defaults for llm/decide actions (default temp: 0.0)",
    )
    generate: LLMActionDefaultsConfig | None = Field(
        default=None,
        description="Defaults for llm/generate actions (default temp: 0.7)",
    )
    instruct: LLMActionDefaultsConfig | None = Field(
        default=None,
        description="Defaults for llm/instruct actions (default temp: 0.7)",
    )


# ==============================================================================
# Input Definitions
# ==============================================================================

class InputDefinition(BaseModel):
    """Definition for a workflow input parameter."""
    description: str | None = None
    type: Literal["string", "number", "boolean", "array", "object", "file"] | None = Field(
        default="string",
        description="Input type: string, number, boolean, array, object, file",
    )
    default: Any | None = None
    required: bool = Field(
        default=True,
        description="Whether this input is required",
    )
    enum: list[Any] | None = Field(
        default=None,
        description="Allowed values (if restricted)",
    )
    pattern: str | None = Field(
        default=None,
        description="Regex pattern for validation (strings only)",
    )
    min_: int | float | None = Field(
        default=None,
        alias="min",
        description="Minimum value for numeric types, or minimum length for arrays",
    )
    max_: int | float | None = Field(
        default=None,
        alias="max",
        description="Maximum value for numeric types, or maximum length for arrays",
    )
    secret: bool = Field(
        default=False,
        description="Whether this input contains sensitive data",
    )

    @model_validator(mode="after")
    def validate_type_constraints(self) -> "InputDefinition":
        """Validate that min/max are only used with appropriate types."""
        import warnings

        # min/max only meaningful for number and array types
        if self.min_ is not None or self.max_ is not None:
            if self.type not in ("number", "array"):
                warnings.warn(
                    f"min/max constraints are only meaningful for 'number' or 'array' types, "
                    f"not '{self.type}'. These constraints will be ignored.",
                    UserWarning,
                    stacklevel=2,
                )

        # pattern only meaningful for string type
        if self.pattern is not None and self.type != "string":
            warnings.warn(
                f"pattern constraint is only meaningful for 'string' type, "
                f"not '{self.type}'. This constraint will be ignored.",
                UserWarning,
                stacklevel=2,
            )

        return self


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
    retry_on: list[str] | None = Field(
        default=None,
        description="Error types to retry on (None = default network errors)",
    )


# ==============================================================================
# Guardrail Configuration (LLM Guard Integration)
# ==============================================================================

class GuardrailsConfig(BaseModel):
    """LLM Guard-based guardrails configuration.

    Supports 12 input scanners and 17 output scanners from llm-guard.
    Can be specified at workflow level (defaults for all steps) or
    step level (overrides workflow defaults).

    Input Scanners: anonymize, prompt_injection, secrets, invisible_text,
        token_limit, ban_topics, ban_substrings, ban_code, code, gibberish,
        language, regex

    Output Scanners: deanonymize, sensitive, no_refusal, factual_consistency,
        relevance, json, malicious_urls, url_reachability, language_same,
        language, reading_time, gibberish, ban_topics, ban_substrings,
        ban_code, code, regex

    Example:
        guardrails:
          input:
            prompt_injection: { threshold: 0.92 }
            secrets: { redact: true }
          output:
            sensitive: { redact: true }
          on_fail: abort
    """

    input: dict[str, dict | None] | None = Field(
        default=None,
        description="Input scanners applied before step execution",
    )
    output: dict[str, dict | None] | None = Field(
        default=None,
        description="Output scanners applied after step execution",
    )
    on_fail: Literal["abort", "retry", "continue"] | str | None = Field(
        default=None,
        description="Action on failure: abort, retry, continue, or step_id to route to",
    )
    max_retries: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="Max retries when on_fail=retry (default: 2)",
    )


# ==============================================================================
# Action-Specific Configurations
# ==============================================================================

class HTTPRequestConfig(BaseModel):
    """Configuration for http/request action."""
    url: str
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"] = "GET"
    headers: dict[str, str] | None = None
    secret_headers: dict[str, str] | None = Field(
        default=None,
        description="Headers read from secrets directory (never logged)",
    )
    body: Any | None = None
    json_body: dict[str, Any] | None = Field(
        default=None,
        alias="json",
        description="JSON body (sets Content-Type automatically)",
    )
    timeout: int = Field(default=30, ge=1, le=3600)
    follow_redirects: bool = True
    verify_ssl: bool = True


class LLMActionConfig(BaseModel):
    """Configuration for llm/* actions."""
    content: str | None = Field(
        default=None,
        alias="input",
        description="Content to analyze/extract from",
    )
    prompt: str | None = Field(
        default=None,
        description="Custom prompt (overrides default)",
    )
    schema_: dict[str, Any] | None = Field(
        default=None,
        alias="schema",
        description="JSON schema for structured output (llm/extract)",
    )
    choices: list[str] | None = Field(
        default=None,
        description="Decision options (llm/decide)",
    )
    model: str | None = Field(
        default=None,
        description="Model override (default: client's configured model)",
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2,
        description="Sampling temperature (default varies by action type)",
    )
    max_tokens: int | None = Field(default=None, ge=1)
    chunking: ChunkingConfig | None = Field(
        default=None,
        description="Chunking configuration for large content",
    )
    aggregation: AggregationConfig | None = Field(
        default=None,
        description="Aggregation configuration for chunked results",
    )


class HumanInputConfig(BaseModel):
    """Configuration for human/input action.

    Note: For choice-based inputs, use human/decide with choices parameter.
    This action is for free-form text input only.
    """
    prompt: str
    input_type: Literal["text", "multiline", "file", "editor"] | None = Field(
        default="text",
        description="Input mode: text (single line), multiline, file (path), editor ($EDITOR)",
    )
    default: str | None = None
    timeout: int | None = Field(
        default=None,
        description="Timeout in seconds (None = wait indefinitely)",
    )
    initial_content: str | None = Field(
        default=None,
        description="Initial content for editor mode",
    )


class HumanDecideConfig(BaseModel):
    """Configuration for human/decide action.

    Use for yes/no confirmation or selection from predefined choices.
    """
    prompt: str
    choices: list[str] | None = Field(
        default=None,
        description="Predefined choices (omit for yes/no confirmation)",
    )
    multi: bool = Field(
        default=False,
        description="Allow multiple selections",
    )
    default: str | None = None
    timeout: int | None = Field(
        default=None,
        description="Timeout in seconds (None = wait indefinitely)",
    )


class ScriptConfig(BaseModel):
    """Configuration for script/* action."""
    path: str | None = Field(
        default=None,
        description="Path to script file",
    )
    inline: str | None = Field(
        default=None,
        description="Inline script content",
    )
    language: Literal["python", "bash", "powershell"] | None = Field(
        default=None,
        description="Script language (auto-detected from path extension)",
    )
    args: list[str] | None = None
    env: dict[str, str] | None = None

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


class StateAppendConfig(BaseModel):
    """Configuration for state/append action."""
    target: str = Field(description="List variable name to append to")
    value: Any = Field(description="Value to append to the list")


# ==============================================================================
# Step Definition
# ==============================================================================

class StepDefinition(BaseModel):
    """Definition for a single workflow step."""

    # Identity
    name: str | None = Field(
        default=None,
        description="Human-readable step name",
    )
    id: str | None = Field(
        default=None,
        description="Step ID for referencing outputs",
    )

    # Action (exactly one of these must be set)
    run: str | list[str] | None = Field(
        default=None,
        description="Shell command (string or array)",
    )
    uses: str | None = Field(
        default=None,
        description="Action type (e.g., 'llm/extract', 'http/request')",
    )

    # Action configuration
    with_: dict[str, Any] | None = Field(
        default=None,
        alias="with",
        description="Action-specific configuration",
    )

    # Conditional execution
    if_: str | None = Field(
        default=None,
        alias="if",
        description="Condition expression (step skipped if false)",
    )

    # Loop iteration
    loop: str | None = Field(
        default=None,
        description="Expression returning list to iterate over",
    )
    break_if: str | None = Field(
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
    capture_stderr: CaptureStderrMode = Field(
        default=CaptureStderrMode.SEPARATE,
        description="How to handle stderr: merge into stdout, keep separate, or discard",
    )

    # Timeout and error handling
    timeout: int = Field(
        default=300,
        ge=1,
        le=86400,
        description="Timeout in seconds (default: 5 minutes)",
    )
    on_failure: str | None = Field(
        default=None,
        description="Step ID to jump to on failure",
    )
    resume_from: str | None = Field(
        default=None,
        description="Where to continue after error handler",
    )

    # Retry configuration
    retry: RetryConfig | None = None

    # Output validation (LLM Guard integration)
    guardrails: GuardrailsConfig | Literal[False] | None = Field(
        default=None,
        description="Step guardrails (merges with workflow defaults, or False to disable)",
    )

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
    def validate_id(cls, v: str | None) -> str | None:
        """Validate step ID format."""
        if v is None:
            return v

        from ..evaluator.security import validate_step_id
        validate_step_id(v)
        return v


# ==============================================================================
# Job Definition
# ==============================================================================

class JobDefinition(BaseModel):
    """Definition for a workflow job."""
    name: str | None = None
    steps: list[StepDefinition]
    finally_: list[StepDefinition] | None = Field(
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
    description: str | None = None
    version: str | None = Field(
        default=None,
        description="Workflow version (semver recommended, e.g., '1.0.0')",
    )
    author: str | None = Field(
        default=None,
        description="Workflow author or maintainer",
    )
    schema_version: str = Field(
        default="1.0",
        description="Schema version for compatibility",
    )

    # Inputs and environment
    inputs: dict[str, InputDefinition | Any] | None = Field(
        default=None,
        description="Input parameter definitions",
    )
    env: dict[str, str] | None = Field(
        default=None,
        description="Environment variables",
    )

    # Jobs
    jobs: dict[str, JobDefinition] = Field(
        description="Job definitions (usually just 'main')",
    )

    # Lifecycle hooks
    finally_: list[StepDefinition] | None = Field(
        default=None,
        alias="finally",
        description="Workflow-level cleanup",
    )
    on_complete: list[StepDefinition] | None = Field(
        default=None,
        description="Steps to run on successful completion",
    )
    on_failure: list[StepDefinition] | None = Field(
        default=None,
        description="Steps to run on workflow failure",
    )

    # Guardrails (LLM Guard integration)
    guardrails: GuardrailsConfig | None = Field(
        default=None,
        description="Default guardrails applied to all steps",
    )

    # LLM configuration
    llm: LLMDefaultsConfig | None = Field(
        default=None,
        description="Default LLM configuration for all actions",
    )

    # Security settings
    shell_safety: ShellSafetyMode = Field(
        default=ShellSafetyMode.STRICT,
        description="Shell injection prevention mode",
    )
    workspace: str | None = Field(
        default=None,
        description="Working directory for the workflow",
    )
    secrets_dir: str | None = Field(
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
        from ..core.types import DEPRECATED_VERSIONS, SUPPORTED_SCHEMA_VERSIONS
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
