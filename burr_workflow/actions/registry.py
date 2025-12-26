"""
Action registry for looking up and instantiating actions.

The registry provides a centralized way to register and resolve
action types, enabling extensibility through plugins.
"""

from functools import lru_cache
from typing import Any, Callable, Optional, Type, TYPE_CHECKING

from ..core.errors import ActionNotFoundError
from .base import BaseAction

if TYPE_CHECKING:
    from ..protocols import ExecutionContext, LLMClient


# Type alias for action factory functions
ActionFactory = Callable[..., BaseAction]


class ActionRegistry:
    """Registry for action types.

    Maps action type names (e.g., 'shell', 'http/request', 'llm/extract')
    to action classes or factory functions.

    Supports:
    - Built-in actions (registered at module load)
    - Plugin actions (registered via ActionProvider protocol)
    - Custom actions (registered at runtime)
    """

    def __init__(self):
        """Initialize an empty registry."""
        self._actions: dict[str, Type[BaseAction] | ActionFactory] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self,
        action_type: str,
        action_class: Type[BaseAction] | ActionFactory,
        *,
        aliases: Optional[list[str]] = None,
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


def _register_builtin_actions(registry: ActionRegistry) -> None:
    """Register built-in actions with the registry."""
    from .shell import ShellAction
    from .http import HTTPAction
    from .state import StateSetAction
    from .control import ExitAction, FailAction
    from .iterator import (
        IteratorInitAction,
        IteratorCheckAction,
        IteratorAdvanceAction,
        IteratorFinalizeAction,
    )

    # Shell commands
    registry.register("shell", ShellAction, aliases=["run"])

    # HTTP requests
    registry.register("http/request", HTTPAction, aliases=["http", "http/get", "http/post"])

    # State manipulation
    registry.register("state/set", StateSetAction, aliases=["set"])

    # Control flow
    registry.register("control/exit", ExitAction, aliases=["exit"])
    registry.register("control/fail", FailAction, aliases=["fail"])

    # Iterator (internal, used by compiler)
    registry.register("__iterator/init", IteratorInitAction)
    registry.register("__iterator/check", IteratorCheckAction)
    registry.register("__iterator/advance", IteratorAdvanceAction)
    registry.register("__iterator/finalize", IteratorFinalizeAction)


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
    from .llm import LLMExtractAction, LLMDecideAction, LLMGenerateAction

    # Create factories that capture the llm_client
    registry.register(
        "llm/extract",
        lambda: LLMExtractAction(llm_client),
        aliases=["extract"],
    )
    registry.register(
        "llm/decide",
        lambda: LLMDecideAction(llm_client),
        aliases=["decide"],
    )
    registry.register(
        "llm/generate",
        lambda: LLMGenerateAction(llm_client),
        aliases=["generate", "llm/analyze"],
    )
