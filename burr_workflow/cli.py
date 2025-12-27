"""
Command-line interface for burr_workflow.

Provides CLI commands:
- workflow-validate: Static analysis of workflow YAML files
- workflow-schema: Export JSON Schema for IDE validation
"""

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from .core.validator import (
    ValidationLevel,
    ValidationResult,
    validate_workflow_yaml,
)
from .schemas.models import WorkflowDefinition


def format_message(msg) -> str:
    """Format a validation message for display."""
    level_colors = {
        ValidationLevel.ERROR: "\033[91m",    # Red
        ValidationLevel.WARNING: "\033[93m",  # Yellow
        ValidationLevel.INFO: "\033[94m",     # Blue
    }
    reset = "\033[0m"

    # Check if terminal supports colors
    use_colors = sys.stderr.isatty()

    level_str = msg.level.value.upper()
    if use_colors:
        color = level_colors.get(msg.level, "")
        level_str = f"{color}{level_str}{reset}"

    parts = [f"[{msg.code}] {level_str}: {msg.message}"]

    if msg.location:
        parts.append(f"  Location: {msg.location}")

    if msg.suggestion:
        parts.append(f"  Suggestion: {msg.suggestion}")

    return "\n".join(parts)


def print_result(result: ValidationResult) -> None:
    """Print validation result to stderr."""
    for msg in result.messages:
        print(format_message(msg), file=sys.stderr)
        print(file=sys.stderr)  # Blank line between messages

    # Summary
    errors = len(result.errors)
    warnings = len(result.warnings)

    if errors or warnings:
        summary = f"Validation complete: {errors} error(s), {warnings} warning(s)"
    else:
        summary = "Validation complete: no issues found"

    print(summary, file=sys.stderr)


def workflow_validate_cmd() -> NoReturn:
    """CLI entrypoint for workflow-validate command.

    Exit codes:
        0: Valid, no errors or warnings
        1: Validation errors
        2: Warnings only (without --strict)
    """
    parser = argparse.ArgumentParser(
        prog="workflow-validate",
        description="Validate a burr_workflow YAML definition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes:
  0  Valid, no errors or warnings
  1  Validation errors found
  2  Warnings only (without --strict)

Examples:
  workflow-validate workflow.yaml
  workflow-validate workflow.yaml --strict
  workflow-validate -q workflow.yaml  # Only show exit code
""",
    )

    parser.add_argument(
        "workflow_file",
        type=Path,
        help="Path to workflow YAML file",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )

    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress output (only return exit code)",
    )

    args = parser.parse_args()

    # Read workflow file
    workflow_path: Path = args.workflow_file

    if not workflow_path.exists():
        if not args.quiet:
            print(f"Error: File not found: {workflow_path}", file=sys.stderr)
        sys.exit(1)

    if not workflow_path.is_file():
        if not args.quiet:
            print(f"Error: Not a file: {workflow_path}", file=sys.stderr)
        sys.exit(1)

    try:
        yaml_content = workflow_path.read_text(encoding="utf-8")
    except Exception as e:
        if not args.quiet:
            print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate
    try:
        result = validate_workflow_yaml(yaml_content, strict=args.strict)
    except Exception as e:
        if not args.quiet:
            print(f"Validation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Print results
    if not args.quiet:
        print_result(result)

    # Determine exit code
    if not result.valid:
        sys.exit(1)
    elif result.warnings and not args.strict:
        sys.exit(2)
    else:
        sys.exit(0)


def workflow_schema_cmd() -> NoReturn:
    """CLI entrypoint for workflow-schema command.

    Exports the JSON Schema for workflow YAML files.
    Can be used for IDE validation and autocomplete.

    Exit codes:
        0: Schema exported successfully
        1: Error occurred
    """
    parser = argparse.ArgumentParser(
        prog="workflow-schema",
        description="Export JSON Schema for workflow YAML validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  workflow-schema                      # Print to stdout
  workflow-schema -o schema.json       # Save to file
  workflow-schema --pretty             # Pretty-print with indentation

Usage in YAML files:
  Add this comment at the top of your workflow YAML:
  # yaml-language-server: $schema=./workflow-schema.json
""",
    )

    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output file path (default: stdout)",
    )

    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print with indentation",
    )

    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress status messages",
    )

    args = parser.parse_args()

    try:
        # Generate schema from Pydantic model
        schema = WorkflowDefinition.model_json_schema()

        # Add $schema meta reference and title
        schema["$schema"] = "http://json-schema.org/draft-07/schema#"
        if "title" not in schema:
            schema["title"] = "Burr Workflow Definition"

        # Format output
        indent = 2 if args.pretty else None
        schema_json = json.dumps(schema, indent=indent)

        if args.output:
            # Write to file
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(schema_json, encoding="utf-8")
            if not args.quiet:
                print(f"Schema written to: {args.output}", file=sys.stderr)
        else:
            # Print to stdout
            print(schema_json)

        sys.exit(0)

    except Exception as e:
        if not args.quiet:
            print(f"Error generating schema: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> NoReturn:
    """Main entry point dispatching to subcommands."""
    # Currently workflow-validate, but extensible for future commands
    workflow_validate_cmd()


if __name__ == "__main__":
    main()
