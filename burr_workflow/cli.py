"""
Command-line interface for burr_workflow.

Provides a unified CLI with subcommands:
- workflow validate: Static analysis of workflow YAML files
- workflow analyze: Show execution flow (static analysis)
- workflow schema: Export JSON Schema for IDE validation
- workflow create: Scaffold new workflow from template
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
from .templates import TEMPLATES, get_template, list_templates


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


def _load_workflow_file(workflow_path: Path, quiet: bool = False) -> Optional[str]:
    """Load and validate workflow file exists.

    Args:
        workflow_path: Path to workflow YAML file
        quiet: Suppress error messages

    Returns:
        File content as string, or None if error (exits with code 1)
    """
    if not workflow_path.exists():
        if not quiet:
            print(f"Error: File not found: {workflow_path}", file=sys.stderr)
        sys.exit(1)

    if not workflow_path.is_file():
        if not quiet:
            print(f"Error: Not a file: {workflow_path}", file=sys.stderr)
        sys.exit(1)

    try:
        return workflow_path.read_text(encoding="utf-8")
    except Exception as e:
        if not quiet:
            print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_validate(args: argparse.Namespace) -> NoReturn:
    """Handle 'workflow validate' subcommand."""
    yaml_content = _load_workflow_file(args.workflow_file, args.quiet)

    # Handle dry-run/analyze mode
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
            print_result(result, str(args.workflow_file))
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
            print_result(result, str(args.workflow_file))
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

    # Standard validation
    try:
        result = validate_workflow_yaml(yaml_content, strict=args.strict)
    except Exception as e:
        if not args.quiet:
            print(f"Validation failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Print results
    if not args.quiet:
        print_result(result, str(args.workflow_file))

    # Determine exit code
    if not result.valid:
        sys.exit(1)
    elif result.warnings and not args.strict:
        sys.exit(2)
    else:
        sys.exit(0)


def cmd_analyze(args: argparse.Namespace) -> NoReturn:
    """Handle 'workflow analyze' subcommand (alias for validate --dry-run)."""
    yaml_content = _load_workflow_file(args.workflow_file, args.quiet)

    # Parse YAML
    try:
        yaml = ruamel.yaml.YAML()
        workflow = yaml.load(yaml_content)
    except Exception as e:
        print(f"Error parsing YAML: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate first
    result = validate_workflow_yaml(yaml_content, strict=False)
    if not result.valid:
        print_result(result, str(args.workflow_file))
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


def cmd_schema(args: argparse.Namespace) -> NoReturn:
    """Handle 'workflow schema' subcommand."""
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


def cmd_create(args: argparse.Namespace) -> NoReturn:
    """Handle 'workflow create' subcommand."""
    # Handle --list-templates
    if args.list_templates:
        print("Available templates:\n")
        for name, desc in sorted(list_templates().items()):
            print(f"  {name:12} - {desc}")
        print("\nUsage: workflow create <name> --template=<template>")
        sys.exit(0)

    # Validate name is provided
    if not args.name:
        print("Error: workflow name is required", file=sys.stderr)
        print("Usage: workflow create <name> [--template=<template>]", file=sys.stderr)
        sys.exit(1)

    # Load template
    try:
        content = get_template(args.template)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Substitute template variables
    # Use {{ name }} instead of ${{ name }} to avoid conflict with workflow expressions
    content = content.replace("{{ name }}", args.name)

    # Determine output path
    # Sanitize name for filename: lowercase, replace spaces with hyphens
    filename = f"{args.name.replace(' ', '-').lower()}.yaml"
    output_path = args.output_dir / filename

    # Check if file exists
    if output_path.exists():
        print(f"Error: File already exists: {output_path}", file=sys.stderr)
        print("Use a different name or delete the existing file.", file=sys.stderr)
        sys.exit(1)

    # Ensure output directory exists
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Error creating directory: {e}", file=sys.stderr)
        sys.exit(1)

    # Write file
    try:
        output_path.write_text(content, encoding="utf-8")
    except Exception as e:
        print(f"Error writing file: {e}", file=sys.stderr)
        sys.exit(1)

    if not args.quiet:
        print(f"Created: {output_path}")
        print(f"\nNext steps:")
        print(f"  1. Edit {output_path} to customize your workflow")
        print(f"  2. Validate: workflow validate {output_path}")
        print(f"  3. Analyze:  workflow analyze {output_path}")

    sys.exit(0)


def main() -> NoReturn:
    """Unified CLI entry point with subcommands."""
    parser = argparse.ArgumentParser(
        prog="workflow",
        description="Workflow engine CLI for validation, analysis, and scaffolding",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Subcommands:
  validate    Validate a workflow YAML file
  analyze     Show execution flow (static analysis)
  schema      Export JSON Schema for IDE validation
  create      Create a new workflow from template

Examples:
  workflow validate my-workflow.yaml
  workflow validate my-workflow.yaml --strict
  workflow analyze my-workflow.yaml --inputs '{"target": "example.com"}'
  workflow schema --pretty -o schema.json
  workflow create my-scan --template=osint
  workflow create --list-templates
""",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # ─────────────────────────────────────────────────────────────────────────
    # validate subcommand
    # ─────────────────────────────────────────────────────────────────────────
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate a workflow YAML file",
        description="Validate a burr_workflow YAML definition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exit codes:
  0  Valid, no errors or warnings
  1  Validation errors found
  2  Warnings only (without --strict)

Examples:
  workflow validate workflow.yaml
  workflow validate workflow.yaml --strict
  workflow validate workflow.yaml --dry-run
  workflow validate workflow.yaml -v  # Generate mermaid diagram
""",
    )
    validate_parser.add_argument(
        "workflow_file",
        type=Path,
        help="Path to workflow YAML file",
    )
    validate_parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as errors",
    )
    validate_parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress output (only return exit code)",
    )
    validate_parser.add_argument(
        "--dry-run", "--analyze",
        dest="dry_run",
        action="store_true",
        help="Show execution flow without running (static analysis)",
    )
    validate_parser.add_argument(
        "--inputs",
        type=str,
        default=None,
        help='JSON string of inputs for display (e.g., \'{"target": "example.com"}\')',
    )
    # Visualization options
    validate_parser.add_argument(
        "--visualize", "-v",
        nargs="?",
        const="-",  # stdout by default
        metavar="OUTPUT",
        help="Generate workflow diagram (default: mermaid to stdout)",
    )
    validate_parser.add_argument(
        "--engine", "-e",
        choices=["mermaid", "graphviz"],
        default="mermaid",
        help="Visualization engine (default: mermaid)",
    )
    validate_parser.add_argument(
        "--format", "-f",
        choices=["md", "png", "svg", "pdf", "dot"],
        default="md",
        help="Output format (default: md for mermaid, png for graphviz)",
    )
    validate_parser.add_argument(
        "--show-conditions",
        action="store_true",
        help="Include transition conditions in diagram",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # analyze subcommand (convenience alias for validate --dry-run)
    # ─────────────────────────────────────────────────────────────────────────
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Show execution flow (static analysis)",
        description="Analyze workflow execution flow without running",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This is equivalent to 'workflow validate --dry-run'.

Examples:
  workflow analyze workflow.yaml
  workflow analyze workflow.yaml --inputs '{"target": "example.com"}'
""",
    )
    analyze_parser.add_argument(
        "workflow_file",
        type=Path,
        help="Path to workflow YAML file",
    )
    analyze_parser.add_argument(
        "--inputs",
        type=str,
        default=None,
        help='JSON string of inputs for display (e.g., \'{"target": "example.com"}\')',
    )
    analyze_parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress error messages",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # schema subcommand
    # ─────────────────────────────────────────────────────────────────────────
    schema_parser = subparsers.add_parser(
        "schema",
        help="Export JSON Schema for IDE validation",
        description="Export JSON Schema for workflow YAML validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  workflow schema                      # Print to stdout
  workflow schema -o schema.json       # Save to file
  workflow schema --pretty             # Pretty-print with indentation

Usage in YAML files:
  Add this comment at the top of your workflow YAML:
  # yaml-language-server: $schema=./workflow-schema.json
""",
    )
    schema_parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output file path (default: stdout)",
    )
    schema_parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print with indentation",
    )
    schema_parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress status messages",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # create subcommand
    # ─────────────────────────────────────────────────────────────────────────
    create_parser = subparsers.add_parser(
        "create",
        help="Create a new workflow from template",
        description="Scaffold a new workflow YAML file from a template",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available templates:
  minimal      Bare minimum valid workflow (default)
  osint        OSINT reconnaissance workflow
  scan         Port scanning with analysis
  credential   Credential testing with loop break
  interactive  Human-in-the-loop workflow
  api          API integration with error handling

Examples:
  workflow create my-scan --template=osint
  workflow create recon -t scan -o ./workflows/
  workflow create --list-templates
""",
    )
    create_parser.add_argument(
        "name",
        nargs="?",
        help="Name for the new workflow",
    )
    create_parser.add_argument(
        "-t", "--template",
        default="minimal",
        choices=list(TEMPLATES.keys()),
        help="Template to use (default: minimal)",
    )
    create_parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("."),
        help="Output directory (default: current directory)",
    )
    create_parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List available templates and exit",
    )
    create_parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress status messages",
    )

    # Parse arguments
    args = parser.parse_args()

    # Handle no command
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    # Dispatch to subcommand handler
    if args.command == "validate":
        cmd_validate(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "schema":
        cmd_schema(args)
    elif args.command == "create":
        cmd_create(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
