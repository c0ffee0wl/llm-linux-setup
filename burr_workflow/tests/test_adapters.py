"""
Tests for BurrActionAdapter state management.

These tests verify reserved state keys and type definitions without
requiring the full Burr package to be installed.

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
_types_path = _package_root / "core" / "types.py"


def _get_types_module():
    """Get types module without triggering full package imports."""
    return _load_module_directly(_types_path, "_types_standalone")


def test_reserved_state_keys_exist():
    """RESERVED_STATE_KEYS should be defined and non-empty."""
    types_module = _get_types_module()
    RESERVED_STATE_KEYS = types_module.RESERVED_STATE_KEYS

    assert isinstance(RESERVED_STATE_KEYS, frozenset)
    assert len(RESERVED_STATE_KEYS) > 0

    # Core control keys must be present
    expected_keys = {
        "__next",
        "__condition_met",
        "__loop_break_requested",
        "__workflow_exit",
        "__workflow_failed",
        "__interrupted",
    }
    for key in expected_keys:
        assert key in RESERVED_STATE_KEYS, f"Missing reserved key: {key}"


def test_guard_state_keys_present():
    """LLM Guard state keys should be in RESERVED_STATE_KEYS."""
    types_module = _get_types_module()
    RESERVED_STATE_KEYS = types_module.RESERVED_STATE_KEYS

    guard_keys = {
        "__guard_vault",
        "__guard_input_content",
        "__guard_scan_results",
        "__guard_warning",
    }
    for key in guard_keys:
        assert key in RESERVED_STATE_KEYS, f"Missing guard key: {key}"


def test_no_legacy_guardrail_keys():
    """Legacy guardrail keys should NOT be in RESERVED_STATE_KEYS."""
    types_module = _get_types_module()
    RESERVED_STATE_KEYS = types_module.RESERVED_STATE_KEYS

    legacy_keys = {
        "__guardrail_next",
        "__guardrail_warning",
        "__guardrail_retry_count",
        "__guardrail_error",
    }
    for key in legacy_keys:
        assert key not in RESERVED_STATE_KEYS, f"Legacy key should be removed: {key}"


def test_step_outcome_enum():
    """StepOutcome should have expected values."""
    types_module = _get_types_module()
    StepOutcome = types_module.StepOutcome

    expected = {"success", "failure", "skipped", "suspended", "partial", "break"}
    actual = {e.value for e in StepOutcome}

    assert expected == actual


def test_loop_context_serialization():
    """LoopContext should serialize to dict correctly."""
    types_module = _get_types_module()
    LoopContext = types_module.LoopContext

    ctx = LoopContext(
        items=["a", "b", "c"],
        item="b",
        index=2,
        index0=1,
        total=3,
        first=False,
        last=False,
        revindex=2,
        revindex0=1,
    )

    # Serialize
    data = ctx.to_dict()
    assert data["item"] == "b"
    assert data["index"] == 2
    assert data["total"] == 3
    assert data["items"] == ["a", "b", "c"]
    assert data["first"] is False
    assert data["last"] is False
    assert data["revindex"] == 2


def test_loop_context_nested():
    """LoopContext should handle nested loops."""
    types_module = _get_types_module()
    LoopContext = types_module.LoopContext

    parent = LoopContext(
        items=[1, 2],
        item=1,
        index=1,
        index0=0,
        total=2,
        first=True,
        last=False,
        revindex=2,
        revindex0=1,
    )

    child = LoopContext(
        items=["a", "b"],
        item="a",
        index=1,
        index0=0,
        total=2,
        first=True,
        last=False,
        revindex=2,
        revindex0=1,
        parent=parent,
    )

    data = child.to_dict()
    assert data["parent"] is not None
    assert data["parent"]["item"] == 1

    # Test that nested structure serializes correctly
    # Note: full roundtrip with from_dict is complex due to internal fields
    assert data["item"] == "a"
    assert data["parent"]["item"] == 1


def test_supported_schema_versions():
    """SUPPORTED_SCHEMA_VERSIONS should be defined."""
    types_module = _get_types_module()

    assert hasattr(types_module, "SUPPORTED_SCHEMA_VERSIONS")
    assert "1.0" in types_module.SUPPORTED_SCHEMA_VERSIONS


if __name__ == "__main__":
    # Run tests directly when executed as script
    print("Running adapter/types tests...")
    test_reserved_state_keys_exist()
    print("  test_reserved_state_keys_exist")
    test_guard_state_keys_present()
    print("  test_guard_state_keys_present")
    test_no_legacy_guardrail_keys()
    print("  test_no_legacy_guardrail_keys")
    test_step_outcome_enum()
    print("  test_step_outcome_enum")
    test_loop_context_serialization()
    print("  test_loop_context_serialization")
    test_loop_context_nested()
    print("  test_loop_context_nested")
    test_supported_schema_versions()
    print("  test_supported_schema_versions")
    print("\nAll adapter/types tests passed!")
