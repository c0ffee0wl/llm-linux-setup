"""Vault state management for anonymize/deanonymize flow.

The Vault stores mappings between original values and anonymized placeholders,
enabling deanonymization in later steps.
"""

from typing import Any, Optional

try:
    from llm_guard.vault import Vault
    LLM_GUARD_AVAILABLE = True
except ImportError:
    LLM_GUARD_AVAILABLE = False
    Vault = None  # type: ignore[misc, assignment]


class VaultManager:
    """Manages vault state across workflow steps.

    The vault is used by the anonymize input scanner to store mappings
    between original values (like names, emails) and their placeholder
    replacements. The deanonymize output scanner uses these mappings
    to restore original values.

    State is serializable for persistence across workflow suspensions.
    """

    def __init__(self) -> None:
        if not LLM_GUARD_AVAILABLE:
            self._vault: Optional["Vault"] = None
        else:
            self._vault = Vault()

    @property
    def vault(self) -> Optional["Vault"]:
        """Get the underlying llm-guard Vault instance."""
        return self._vault

    def serialize(self) -> dict[str, Any]:
        """Serialize vault for state persistence.

        Returns:
            Dict containing vault entries for restoration.
        """
        if not self._vault:
            return {}

        # Try public API first (future-proof)
        if hasattr(self._vault, 'get_entries'):
            return {"entries": list(self._vault.get_entries().items())}

        # Fall back to internal API with warning
        try:
            entries = list(self._vault._entries.items())
            return {"entries": entries, "_api_version": "internal"}
        except AttributeError as e:
            import warnings
            warnings.warn(
                f"llm-guard Vault API changed. Vault serialization unavailable. "
                f"Consider pinning llm-guard version. Error: {e}",
                RuntimeWarning,
                stacklevel=2,
            )
            return {}

    def restore(self, data: dict[str, Any]) -> None:
        """Restore vault from serialized state.

        Args:
            data: Serialized vault data from serialize().
        """
        if not self._vault or not data:
            return

        entries = data.get("entries", [])
        if not entries:
            return

        # Try public API first (future-proof)
        if hasattr(self._vault, 'set_entries'):
            self._vault.set_entries(dict(entries))
            return

        # Fall back to internal API
        try:
            for key, value in entries:
                self._vault._entries[key] = value
        except AttributeError as e:
            import warnings
            warnings.warn(
                f"llm-guard Vault API changed. Vault restore unavailable. "
                f"Consider pinning llm-guard version. Error: {e}",
                RuntimeWarning,
                stacklevel=2,
            )

    def clear(self) -> None:
        """Clear all vault entries."""
        if self._vault:
            try:
                self._vault._entries.clear()
            except AttributeError:
                pass
