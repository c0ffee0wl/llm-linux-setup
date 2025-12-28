"""
Tests for filter consistency between validator and runtime.

Ensures that all SAFE_FILTERS available at runtime are also whitelisted
in the validator's ALLOWED_FILTERS. This prevents the situation where
a workflow passes validation but fails at runtime due to "unknown filter".

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
_filters_path = _package_root / "evaluator" / "filters.py"
_validator_path = _package_root / "core" / "validator.py"


def _get_safe_filters():
    """Get SAFE_FILTERS from filters.py without triggering package imports."""
    filters_module = _load_module_directly(_filters_path, "_filters_standalone")
    return filters_module.SAFE_FILTERS


def _get_allowed_filters():
    """Get ALLOWED_FILTERS from validator.py without triggering package imports."""
    # The validator.py imports SAFE_FILTERS from burr_workflow.evaluator.filters,
    # so we need to set up that dependency first
    _load_module_directly(_filters_path, "burr_workflow.evaluator.filters")

    # Now load validator
    validator_module = _load_module_directly(_validator_path, "_validator_standalone")
    # ALLOWED_FILTERS is a class attribute on Jinja2ExpressionValidator
    return validator_module.Jinja2ExpressionValidator.ALLOWED_FILTERS


def test_validator_includes_all_safe_filters():
    """Validator ALLOWED_FILTERS must include all runtime SAFE_FILTERS."""
    SAFE_FILTERS = _get_safe_filters()
    ALLOWED_FILTERS = _get_allowed_filters()

    missing_filters = set(SAFE_FILTERS.keys()) - ALLOWED_FILTERS

    assert not missing_filters, (
        f"SAFE_FILTERS available at runtime but missing from validator's ALLOWED_FILTERS: "
        f"{sorted(missing_filters)}. "
        f"Workflows using these filters will fail validation but work at runtime. "
        f"Update ALLOWED_FILTERS in core/validator.py to include these filters."
    )


def test_validator_includes_builtin_jinja2_filters():
    """Validator ALLOWED_FILTERS must include common Jinja2 built-in filters."""
    ALLOWED_FILTERS = _get_allowed_filters()

    # Core Jinja2 filters that should always be available
    builtin_filters = {
        # Collection filters
        "length",
        "first",
        "last",
        "sort",
        "unique",
        "join",
        "keys",
        "values",
        # String filters
        "lower",
        "upper",
        "trim",
        "split",
        # Type conversion
        "int",
        "float",
        "string",
        # Default value
        "default",
    }

    missing_filters = builtin_filters - ALLOWED_FILTERS

    assert not missing_filters, (
        f"Jinja2 built-in filters missing from validator's ALLOWED_FILTERS: "
        f"{sorted(missing_filters)}. "
        f"These are standard Jinja2 filters that users expect to work."
    )


def test_safe_filters_are_callable():
    """All SAFE_FILTERS entries must be callable."""
    SAFE_FILTERS = _get_safe_filters()

    for name, func in SAFE_FILTERS.items():
        assert callable(func), (
            f"SAFE_FILTERS['{name}'] is not callable: {type(func)}. "
            f"All filter entries must be functions."
        )


def test_gha_compatible_filters_in_validator():
    """GitHub Actions compatible filters should be whitelisted in validator.

    Note: These filters are documented as available (see CLAUDE.md) but some
    may be provided by Jinja2 built-ins rather than SAFE_FILTERS.
    """
    ALLOWED_FILTERS = _get_allowed_filters()

    gha_filters = {
        "contains",
        "startsWith",
        "endsWith",
        "format",
        "toJSON",
        "fromJSON",
    }

    # Check they're in validator - this is what matters for validation
    missing_from_validator = gha_filters - ALLOWED_FILTERS
    assert not missing_from_validator, (
        f"GHA-compatible filters missing from ALLOWED_FILTERS: {sorted(missing_from_validator)}"
    )


def test_security_filters_available():
    """Security-related filters should be available for pentest workflows."""
    ALLOWED_FILTERS = _get_allowed_filters()
    SAFE_FILTERS = _get_safe_filters()

    # Core security filters that MUST be in both validator and runtime
    security_filters = {
        "shell_quote",
        "is_valid_ip",
        "is_private_ip",
        "in_cidr",
    }

    # Check they're in validator
    missing_from_validator = security_filters - ALLOWED_FILTERS
    assert not missing_from_validator, (
        f"Security filters missing from ALLOWED_FILTERS: {sorted(missing_from_validator)}"
    )

    # Check they're in runtime
    missing_from_runtime = security_filters - set(SAFE_FILTERS.keys())
    assert not missing_from_runtime, (
        f"Security filters missing from SAFE_FILTERS: {sorted(missing_from_runtime)}"
    )


if __name__ == "__main__":
    # Run tests directly when executed as script
    print("Running filter consistency tests...")
    test_validator_includes_all_safe_filters()
    print("✓ test_validator_includes_all_safe_filters")
    test_validator_includes_builtin_jinja2_filters()
    print("✓ test_validator_includes_builtin_jinja2_filters")
    test_safe_filters_are_callable()
    print("✓ test_safe_filters_are_callable")
    test_gha_compatible_filters_in_validator()
    print("✓ test_gha_compatible_filters_in_validator")
    test_security_filters_available()
    print("✓ test_security_filters_available")
    print("\nAll filter consistency tests passed!")
