"""
Configuration settings using pydantic-settings.

Supports configuration via environment variables and
~/.config/burr_workflow/config.toml file.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkflowSettings(BaseSettings):
    """Workflow engine configuration settings.

    Configuration is loaded from (in order of priority):
    1. Environment variables (BURR_WORKFLOW_*)
    2. Config file (~/.config/burr_workflow/config.toml)
    3. Default values
    """

    model_config = SettingsConfigDict(
        env_prefix="BURR_WORKFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage paths
    data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".config" / "burr_workflow",
        description="Directory for workflow data storage",
    )
    db_path: Path | None = Field(
        default=None,
        description="SQLite database path (default: data_dir/workflows.db)",
    )
    secrets_dir: Path | None = Field(
        default=None,
        description="Directory containing secret files",
    )

    # Default timeouts
    default_timeout: int = Field(
        default=300,
        ge=1,
        description="Default step timeout in seconds (5 minutes)",
    )
    shell_timeout: int = Field(
        default=300,
        ge=1,
        description="Default shell command timeout",
    )
    http_timeout: int = Field(
        default=30,
        ge=1,
        description="Default HTTP request timeout",
    )
    llm_timeout: int = Field(
        default=120,
        ge=1,
        description="Default LLM action timeout",
    )

    # Loop limits
    max_iterations: int = Field(
        default=10000,
        ge=1,
        description="Maximum loop iterations (safety limit)",
    )
    max_results: int = Field(
        default=100,
        ge=0,
        description="Maximum results to keep in state",
    )
    max_errors: int = Field(
        default=50,
        ge=0,
        description="Maximum errors to accumulate",
    )

    # Security
    shell_safety_strict: bool = Field(
        default=True,
        description="Enforce strict shell safety (unquoted vars are errors)",
    )
    allow_path_escape: bool = Field(
        default=False,
        description="Allow paths outside workspace (DANGEROUS)",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )
    log_step_output: bool = Field(
        default=True,
        description="Log step output to console",
    )

    # Persistence
    checkpoint_frequency: str = Field(
        default="per_step",
        description="When to checkpoint state: per_step, per_loop_iteration",
    )
    persist_state: bool = Field(
        default=True,
        description="Whether to persist workflow state",
    )

    # Burr tracking
    enable_tracking: bool = Field(
        default=False,
        description="Enable Burr UI tracking",
    )
    tracking_project: str = Field(
        default="burr_workflow",
        description="Burr tracking project name",
    )

    def get_db_path(self) -> Path:
        """Get the SQLite database path."""
        if self.db_path:
            return self.db_path
        return self.data_dir / "workflows.db"

    def ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.secrets_dir:
            self.secrets_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> WorkflowSettings:
    """Get cached settings instance.

    Returns:
        WorkflowSettings singleton
    """
    settings = WorkflowSettings()
    settings.ensure_directories()
    return settings
