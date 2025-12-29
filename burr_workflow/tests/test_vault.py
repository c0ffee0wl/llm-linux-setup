"""
Tests for vault state persistence.

The VaultManager enables anonymize/deanonymize flows by storing mappings
between original values and their placeholders. These tests verify
serialization/deserialization works correctly.

Note: These tests load modules directly via importlib to avoid the full
package import chain which requires burr to be installed.
"""

import importlib.util
import sys
from pathlib import Path


def _load_module_directly(module_path: Path, module_name: str):
    """Load a module directly without going through package __init__.py"""
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Get paths to the specific modules
_package_root = Path(__file__).parent.parent
_vault_path = _package_root / "guard" / "vault.py"


def _get_vault_module():
    """Get vault module without triggering full package imports."""
    return _load_module_directly(_vault_path, "_vault_standalone")


def test_llm_guard_availability_constant():
    """LLM_GUARD_AVAILABLE constant should be defined."""
    vault_module = _get_vault_module()
    assert hasattr(vault_module, "LLM_GUARD_AVAILABLE")
    # Can be True or False depending on environment
    assert isinstance(vault_module.LLM_GUARD_AVAILABLE, bool)


def test_vault_manager_class_exists():
    """VaultManager class should be defined."""
    vault_module = _get_vault_module()
    assert hasattr(vault_module, "VaultManager")
    VaultManager = vault_module.VaultManager
    assert callable(VaultManager)


def test_vault_manager_init():
    """VaultManager should initialize without error."""
    vault_module = _get_vault_module()
    VaultManager = vault_module.VaultManager

    vm = VaultManager()
    # vault property exists
    assert hasattr(vm, "vault")
    # _vault attribute exists
    assert hasattr(vm, "_vault")


def test_vault_manager_serialize_empty():
    """Empty vault should serialize to dict."""
    vault_module = _get_vault_module()
    VaultManager = vault_module.VaultManager

    vm = VaultManager()
    result = vm.serialize()

    assert isinstance(result, dict)


def test_vault_manager_restore_empty():
    """Restoring empty data should not raise."""
    vault_module = _get_vault_module()
    VaultManager = vault_module.VaultManager

    vm = VaultManager()
    vm.restore({})  # Should not raise
    vm.restore({"entries": []})  # Should not raise


def test_vault_manager_clear():
    """Clear method should exist and not raise."""
    vault_module = _get_vault_module()
    VaultManager = vault_module.VaultManager

    vm = VaultManager()
    vm.clear()  # Should not raise


def test_vault_manager_has_required_methods():
    """VaultManager should have all required methods."""
    vault_module = _get_vault_module()
    VaultManager = vault_module.VaultManager

    required_methods = ["serialize", "restore", "clear"]
    for method in required_methods:
        assert hasattr(VaultManager, method), f"Missing method: {method}"
        assert callable(getattr(VaultManager, method)), f"Not callable: {method}"


def test_vault_roundtrip_when_available():
    """Test serialize/restore roundtrip if llm-guard is available."""
    vault_module = _get_vault_module()
    VaultManager = vault_module.VaultManager
    LLM_GUARD_AVAILABLE = vault_module.LLM_GUARD_AVAILABLE

    if not LLM_GUARD_AVAILABLE:
        # Skip detailed test if llm-guard not installed
        return

    vm = VaultManager()

    # Add test entry via internal API
    if vm.vault and hasattr(vm.vault, '_entries'):
        vm.vault._entries["[TEST_1]"] = "test_value"

        # Serialize
        serialized = vm.serialize()
        assert "entries" in serialized

        # Restore to new vault
        vm2 = VaultManager()
        vm2.restore(serialized)

        # Verify roundtrip
        assert vm2.vault._entries.get("[TEST_1]") == "test_value"


if __name__ == "__main__":
    # Run tests directly when executed as script
    print("Running vault tests...")
    test_llm_guard_availability_constant()
    print("  test_llm_guard_availability_constant")
    test_vault_manager_class_exists()
    print("  test_vault_manager_class_exists")
    test_vault_manager_init()
    print("  test_vault_manager_init")
    test_vault_manager_serialize_empty()
    print("  test_vault_manager_serialize_empty")
    test_vault_manager_restore_empty()
    print("  test_vault_manager_restore_empty")
    test_vault_manager_clear()
    print("  test_vault_manager_clear")
    test_vault_manager_has_required_methods()
    print("  test_vault_manager_has_required_methods")
    test_vault_roundtrip_when_available()
    print("  test_vault_roundtrip_when_available")
    print("\nAll vault tests passed!")
