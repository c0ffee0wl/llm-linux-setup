"""
Action registry for looking up and instantiating actions.

The registry provides a centralized way to register and resolve
action types, enabling extensibility through plugins.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Optional

from ..core.errors import ActionNotFoundError
from ..protocols import ActionProvider
from .base import BaseAction

if TYPE_CHECKING:
    from ..protocols import ExecutionContext, LLMClient, ReportBackend


# Type alias for action factory functions
ActionFactory = Callable[..., BaseAction]


class ActionRegistry(ActionProvider):
    """Registry for action types.

    Implements the ActionProvider protocol for type-checking while providing
    additional features (aliases, factory functions, dependency injection).

    Maps action type names (e.g., 'shell', 'http/request', 'llm/extract')
    to action classes or factory functions.

    Supports:
    - Built-in actions (registered at module load)
    - Plugin actions (registered via ActionProvider protocol)
    - Custom actions (registered at runtime)
    """

    def __init__(self):
        """Initialize an empty registry."""
        self._actions: dict[str, type[BaseAction] | ActionFactory] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self,
        action_type: str,
        action_class: type[BaseAction] | ActionFactory,
        *,
        aliases: list[str] | None = None,
    ) -> None:
        """Register an action type.

        Args:
            action_type: Action type name (e.g., 'shell', 'http/request')
            action_class: Action class or factory function
            aliases: Optional alternative names for the action
        """
        self._actions[action_type] = action_class

        if aliases:
            for alias in aliases:
                self._aliases[alias] = action_type

    def unregister(self, action_type: str) -> bool:
        """Unregister an action type.

        Args:
            action_type: Action type to remove

        Returns:
            True if removed, False if not found
        """
        if action_type in self._actions:
            del self._actions[action_type]
            # Remove any aliases pointing to this action
            self._aliases = {
                k: v for k, v in self._aliases.items()
                if v != action_type
            }
            return True
        return False

    def get(
        self,
        action_type: str,
        *,
        exec_context: Optional["ExecutionContext"] = None,
        llm_client: Optional["LLMClient"] = None,
    ) -> BaseAction:
        """Get an action instance by type.

        Args:
            action_type: Action type name
            exec_context: Optional execution context for action initialization
            llm_client: Optional LLM client for LLM actions

        Returns:
            Action instance

        Raises:
            ActionNotFoundError: If action type not registered
        """
        # Resolve alias if present
        resolved_type = self._aliases.get(action_type, action_type)

        if resolved_type not in self._actions:
            raise ActionNotFoundError(
                resolved_type,
                available_actions=self.list_actions(),
            )

        action_class = self._actions[resolved_type]

        # Handle factory functions vs classes
        if callable(action_class):
            # Check if it needs special arguments
            import inspect
            sig = inspect.signature(action_class)
            params = sig.parameters

            kwargs: dict[str, Any] = {}
            if "exec_context" in params and exec_context:
                kwargs["exec_context"] = exec_context
            if "llm_client" in params and llm_client:
                kwargs["llm_client"] = llm_client

            return action_class(**kwargs)  # type: ignore

        return action_class()  # type: ignore

    def has(self, action_type: str) -> bool:
        """Check if action type is registered.

        Args:
            action_type: Action type name

        Returns:
            True if registered
        """
        resolved_type = self._aliases.get(action_type, action_type)
        return resolved_type in self._actions

    def list_actions(self) -> list[str]:
        """List all registered action types.

        Returns:
            Sorted list of action type names
        """
        return sorted(self._actions.keys())

    def list_aliases(self) -> dict[str, str]:
        """List all registered aliases.

        Returns:
            Dict mapping alias to action type
        """
        return dict(self._aliases)

    # ActionProvider protocol methods

    def get_action(self, action_type: str) -> type | None:
        """Get action class by type name (ActionProvider protocol).

        Unlike get(), this returns the class/factory, not an instance.

        Args:
            action_type: Action type (e.g., 'shell', 'llm/extract')

        Returns:
            Action class or factory, or None if not found
        """
        resolved_type = self._aliases.get(action_type, action_type)
        return self._actions.get(resolved_type)

    def register_action(self, action_type: str, action_class: type) -> None:
        """Register custom action type (ActionProvider protocol).

        Simpler interface than register() - no alias support.

        Args:
            action_type: Action type name
            action_class: Action class implementing BaseAction
        """
        self._actions[action_type] = action_class


def _register_builtin_actions(registry: ActionRegistry) -> None:
    """Register built-in actions with the registry."""
    from .control import BreakAction, ContinueAction, ExitAction, FailAction, WaitAction
    from .file import FileReadAction, FileWriteAction
    from .http import HTTPAction
    from .human import HumanDecideAction, HumanInputAction
    from .notify import NotifyDesktopAction, NotifyWebhookAction
    from .parse import ParseJSONAction, ParseRegexAction
    from .script import BashScriptAction, PythonScriptAction
    from .shell import ShellAction
    from .state import StateAppendAction, StateSetAction

    # Shell commands
    registry.register("shell", ShellAction, aliases=["run"])

    # HTTP requests
    registry.register("http/request", HTTPAction, aliases=["http", "http/get", "http/post"])

    # State manipulation
    registry.register("state/set", StateSetAction, aliases=["set"])
    registry.register("state/append", StateAppendAction, aliases=["append"])

    # Control flow
    registry.register("control/exit", ExitAction, aliases=["exit"])
    registry.register("control/fail", FailAction, aliases=["fail"])
    registry.register("control/break", BreakAction, aliases=["break"])
    registry.register("control/continue", ContinueAction, aliases=["continue"])
    registry.register("control/wait", WaitAction, aliases=["wait"])

    # Human input (interactive workflows)
    registry.register("human/input", HumanInputAction, aliases=["input"])
    registry.register("human/decide", HumanDecideAction, aliases=["human"])

    # File operations
    registry.register("file/read", FileReadAction, aliases=["read"])
    registry.register("file/write", FileWriteAction, aliases=["write"])

    # Parse operations
    registry.register("parse/json", ParseJSONAction, aliases=["json"])
    registry.register("parse/regex", ParseRegexAction, aliases=["regex"])

    # Notifications
    registry.register("notify/desktop", NotifyDesktopAction, aliases=["notify"])
    registry.register("notify/webhook", NotifyWebhookAction, aliases=["webhook"])

    # Script execution
    registry.register("script/python", PythonScriptAction, aliases=["python"])
    registry.register("script/bash", BashScriptAction, aliases=["bash"])


def get_default_registry(
    llm_client: Optional["LLMClient"] = None,
    cached: bool = True,
) -> ActionRegistry:
    """Get an action registry with built-in actions.

    Args:
        llm_client: Optional LLM client for LLM actions
        cached: If True, return a cached copy (without LLM actions)
                If False, create a fresh registry

    Returns:
        ActionRegistry with built-in actions registered
    """
    if cached and llm_client is None:
        return _get_cached_registry()

    # Create a fresh registry with all actions
    registry = ActionRegistry()
    _register_builtin_actions(registry)

    if llm_client is not None:
        register_llm_actions(registry, llm_client)

    return registry


@lru_cache
def _get_cached_registry() -> ActionRegistry:
    """Get cached registry singleton (internal use)."""
    registry = ActionRegistry()
    _register_builtin_actions(registry)
    return registry


def register_llm_actions(
    registry: ActionRegistry,
    llm_client: "LLMClient",
) -> None:
    """Register LLM actions with a specific client.

    Args:
        registry: Registry to add actions to
        llm_client: LLM client for action execution
    """
    from .llm import (
        LLMDecideAction,
        LLMExtractAction,
        LLMGenerateAction,
        LLMInstructAction,
    )

    # Create factories that capture the llm_client
    registry.register(
        "llm/extract",
        lambda: LLMExtractAction(llm_client),
        aliases=["extract"],
    )
    registry.register(
        "llm/decide",
        lambda: LLMDecideAction(llm_client),
    )
    registry.register(
        "llm/generate",
        lambda: LLMGenerateAction(llm_client),
        aliases=["generate", "analyze", "llm/analyze"],
    )
    registry.register(
        "llm/instruct",
        lambda: LLMInstructAction(llm_client),
        aliases=["instruct"],
    )


def register_report_actions(
    registry: ActionRegistry,
    report_backend: "ReportBackend",
) -> None:
    """Register report actions with a specific backend.

    Args:
        registry: Registry to add actions to
        report_backend: Report backend for finding storage
    """
    from .report import ReportAddAction, ReportListAction

    # Create factories that capture the report_backend
    registry.register(
        "report/add",
        lambda: ReportAddAction(report_backend),
        aliases=["report"],
    )
    registry.register(
        "report/list",
        lambda: ReportListAction(report_backend),
    )
