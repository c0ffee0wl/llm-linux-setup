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
from typing import Any, NoReturn, Optional

import ruamel.yaml

from .core.flow_analyzer import FlowAnalyzer, FlowAnalysisResult
from .core.validator import (
    ValidationLevel,
    ValidationResult,
    validate_workflow_yaml,
)
from .schemas.models import WorkflowDefinition


def format_message(msg, workflow_file: str = "") -> str:
    """Format a validation message for display.

    Args:
        msg: ValidationMessage to format
        workflow_file: Path to workflow file for source location prefix
    """
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

    # Build header with optional source location (file:line:column format)
    header_parts = []
    if workflow_file and msg.source_line is not None:
        if msg.source_column is not None:
            header_parts.append(f"{workflow_file}:{msg.source_line}:{msg.source_column}")
        else:
            header_parts.append(f"{workflow_file}:{msg.source_line}")

    parts = [f"[{msg.code}] {level_str}: {msg.message}"]

    # Show source location if available
    if header_parts:
        parts.insert(0, header_parts[0])

    if msg.location:
        parts.append(f"  Path: {msg.location}")

    if msg.suggestion:
        parts.append(f"  Suggestion: {msg.suggestion}")

    return "\n".join(parts)


def print_result(result: ValidationResult, workflow_file: str = "") -> None:
    """Print validation result to stderr.

    Args:
        result: ValidationResult to print
        workflow_file: Path to workflow file for source location prefix
    """
    for msg in result.messages:
        print(format_message(msg, workflow_file), file=sys.stderr)
        print(file=sys.stderr)  # Blank line between messages

    # Summary
    errors = len(result.errors)
    warnings = len(result.warnings)

    if errors or warnings:
        summary = f"Validation complete: {errors} error(s), {warnings} warning(s)"
    else:
        summary = "Validation complete: no issues found"

    print(summary, file=sys.stderr)


def print_flow_analysis(
    result: FlowAnalysisResult,
    inputs: Optional[dict[str, Any]] = None,
) -> None:
    """Print flow analysis in human-readable format.

    Args:
        result: FlowAnalysisResult from FlowAnalyzer
        inputs: Optional input values for display
    """
    use_colors = sys.stdout.isatty()

    # Color codes
    bold = "\033[1m" if use_colors else ""
    dim = "\033[2m" if use_colors else ""
    yellow = "\033[93m" if use_colors else ""
    cyan = "\033[96m" if use_colors else ""
    green = "\033[92m" if use_colors else ""
    reset = "\033[0m" if use_colors else ""

    # Header
    version_str = f" v{result.workflow_version}" if result.workflow_version else ""
    print(f"\n{bold}Dry Run: {result.workflow_name}{version_str}{reset}")

    # Inputs
    if inputs:
        print(f"{dim}Inputs: {json.dumps(inputs)}{reset}")
    print()

    # Separator
    print("Step Execution Flow:")
    print("━" * 60)
    print()

    # Steps
    for i, step in enumerate(result.steps, 1):
        # Step header
        suffix = ""
        if step.is_loop:
            suffix = f" {cyan}(loop){reset}"
        elif step.is_conditional:
            suffix = f" {yellow}(conditional){reset}"

        print(f"{bold}[{i}] {step.step_id}{reset}{suffix}")

        # Name if different from id
        if step.step_name and step.step_name != step.step_id:
            print(f"    {dim}name: {step.step_name}{reset}")

        # Condition
        if step.condition:
            print(f"    {yellow}if: {step.condition}{reset}")

        # Loop expression
        if step.loop_expr:
            print(f"    {cyan}loop: {step.loop_expr}{reset}")

        # Action
        if step.step_type == "run":
            # Truncate long commands
            action = step.action
            if len(action) > 60:
                action = action[:57] + "..."
            print(f"    run: {action}")
        elif step.step_type == "uses":
            print(f"    uses: {step.action}")

        # Flow analysis
        if step.is_conditional:
            print(f"    {dim}→ CONDITIONAL: depends on runtime value{reset}")
            if step.next_steps:
                next_str = ", ".join(step.next_steps)
                print(f"    {dim}→ if true: execute → {next_str}{reset}")
                print(f"    {dim}→ if false: skip → {next_str}{reset}")
        elif step.is_loop:
            print(f"    {dim}→ LOOP: iterates over {step.loop_expr}{reset}")
            if step.max_iterations:
                print(f"    {dim}→ max_iterations: {step.max_iterations}{reset}")
            if step.next_steps:
                next_str = ", ".join(step.next_steps)
                print(f"    {dim}→ next: {next_str}{reset}")
        else:
            print(f"    {green}→ EXECUTES (unconditional){reset}")
            if step.next_steps:
                next_str = ", ".join(step.next_steps)
                print(f"    {dim}→ next: {next_str}{reset}")

        # On failure handler
        if step.on_failure:
            print(f"    {dim}→ on_failure: {step.on_failure}{reset}")

        print()

    # Summary
    print("Summary:")
    print(f"  {result.total_steps} steps total")
    if result.conditional_count:
        print(f"  {result.conditional_count} conditional")
    if result.loop_count:
        print(f"  {result.loop_count} loop")
    if result.has_finally:
        print(f"  {green}has finally block{reset}")
    print()

    # Data dependencies
    if result.data_dependencies:
        print("Data Dependencies:")
        for step_id, deps in result.data_dependencies.items():
            deps_str = ", ".join(f"{d}.outputs" for d in sorted(deps))
            print(f"  {step_id} ← {deps_str}")
        print()


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

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show execution flow without running (static analysis)",
    )

    parser.add_argument(
        "--inputs",
        type=str,
        default=None,
        help='JSON string of inputs for display (e.g., \'{"target": "example.com"}\')',
    )

    # Visualization options
    parser.add_argument(
        "--visualize", "-v",
        nargs="?",
        const="-",  # stdout by default
        metavar="OUTPUT",
        help="Generate workflow diagram (default: mermaid to stdout)",
    )
    parser.add_argument(
        "--engine", "-e",
        choices=["mermaid", "graphviz"],
        default="mermaid",
        help="Visualization engine (default: mermaid)",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["md", "png", "svg", "pdf", "dot"],
        default="md",
        help="Output format (default: md for mermaid, png for graphviz)",
    )
    parser.add_argument(
        "--show-conditions",
        action="store_true",
        help="Include transition conditions in diagram",
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

    # Handle dry-run mode
    if args.dry_run:
        # Parse YAML first
        try:
            yaml = ruamel.yaml.YAML()
            workflow = yaml.load(yaml_content)
        except Exception as e:
            print(f"Error parsing YAML: {e}", file=sys.stderr)
            sys.exit(1)

        # Still validate to catch errors
        result = validate_workflow_yaml(yaml_content, strict=args.strict)
        if not result.valid:
            print_result(result, str(workflow_path))
            sys.exit(1)

        # Run flow analysis
        analyzer = FlowAnalyzer(workflow)
        flow_result = analyzer.analyze()

        # Parse inputs if provided
        inputs = None
        if args.inputs:
            try:
                inputs = json.loads(args.inputs)
            except json.JSONDecodeError as e:
                print(f"Error parsing --inputs JSON: {e}", file=sys.stderr)
                sys.exit(1)

        print_flow_analysis(flow_result, inputs)
        sys.exit(0)

    # Handle visualization mode
    if args.visualize is not None:
        # Parse YAML first
        try:
            yaml = ruamel.yaml.YAML()
            workflow = yaml.load(yaml_content)
        except Exception as e:
            print(f"Error parsing YAML: {e}", file=sys.stderr)
            sys.exit(1)

        # Validate to catch errors
        result = validate_workflow_yaml(yaml_content, strict=args.strict)
        if not result.valid:
            print_result(result, str(workflow_path))
            sys.exit(1)

        # Compile to get the Burr Application
        try:
            from burr_workflow import WorkflowCompiler
            from burr_workflow.core.visualize import visualize

            compiler = WorkflowCompiler()
            app = compiler.compile(workflow)

            # Determine output path
            output_path = None if args.visualize == "-" else Path(args.visualize)

            # Generate visualization
            result_str = visualize(
                app,
                output_path=output_path,
                engine=args.engine,
                format=args.format,
                include_conditions=args.show_conditions,
            )

            if args.visualize == "-":
                print(result_str)  # Mermaid to stdout
            elif not args.quiet:
                print(f"Generated: {result_str}", file=sys.stderr)

            sys.exit(0)

        except ImportError as e:
            if "graphviz" in str(e).lower():
                print(
                    "Error: graphviz not installed. "
                    "Run: pip install burr_workflow[viz]",
                    file=sys.stderr,
                )
            else:
                print(f"Import error: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"Visualization error: {e}", file=sys.stderr)
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
        print_result(result, str(workflow_path))

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
