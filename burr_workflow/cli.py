"""
Command-line interface for burr_workflow.

Provides a unified CLI with subcommands:
- workflow validate: Static analysis of workflow YAML files
- workflow analyze: Show execution flow (static analysis)
- workflow schema: Export JSON Schema for IDE validation
- workflow create: Scaffold new workflow from template
"""

import json
import sys
from pathlib import Path
from typing import Any, Optional

import click
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
        ValidationLevel.ERROR: "red",
        ValidationLevel.WARNING: "yellow",
        ValidationLevel.INFO: "blue",
    }

    level_str = msg.level.value.upper()
    color = level_colors.get(msg.level)
    level_str = click.style(level_str, fg=color)

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
    """Print validation result to stderr."""
    for msg in result.messages:
        click.echo(format_message(msg, workflow_file), err=True)
        click.echo(err=True)  # Blank line between messages

    # Summary
    errors = len(result.errors)
    warnings = len(result.warnings)

    if errors or warnings:
        summary = f"Validation complete: {errors} error(s), {warnings} warning(s)"
    else:
        summary = click.style("Validation complete: no issues found", fg="green")

    click.echo(summary, err=True)


def print_flow_analysis(
    result: FlowAnalysisResult,
    inputs: Optional[dict[str, Any]] = None,
) -> None:
    """Print flow analysis in human-readable format."""
    # Header
    version_str = f" v{result.workflow_version}" if result.workflow_version else ""
    click.echo(f"\n{click.style(f'Dry Run: {result.workflow_name}{version_str}', bold=True)}")

    # Inputs
    if inputs:
        click.echo(click.style(f"Inputs: {json.dumps(inputs)}", dim=True))
    click.echo()

    # Separator
    click.echo("Step Execution Flow:")
    click.echo("━" * 60)
    click.echo()

    # Steps
    for i, step in enumerate(result.steps, 1):
        # Step header
        suffix = ""
        if step.is_loop:
            suffix = click.style(" (loop)", fg="cyan")
        elif step.is_conditional:
            suffix = click.style(" (conditional)", fg="yellow")

        click.echo(f"{click.style(f'[{i}] {step.step_id}', bold=True)}{suffix}")

        # Name if different from id
        if step.step_name and step.step_name != step.step_id:
            click.echo(click.style(f"    name: {step.step_name}", dim=True))

        # Condition
        if step.condition:
            click.echo(click.style(f"    if: {step.condition}", fg="yellow"))

        # Loop expression
        if step.loop_expr:
            click.echo(click.style(f"    loop: {step.loop_expr}", fg="cyan"))

        # Action
        if step.step_type == "run":
            # Truncate long commands
            action = step.action
            if len(action) > 60:
                action = action[:57] + "..."
            click.echo(f"    run: {action}")
        elif step.step_type == "uses":
            click.echo(f"    uses: {step.action}")

        # Flow analysis
        if step.is_conditional:
            click.echo(click.style("    → CONDITIONAL: depends on runtime value", dim=True))
            if step.next_steps:
                next_str = ", ".join(step.next_steps)
                click.echo(click.style(f"    → if true: execute → {next_str}", dim=True))
                click.echo(click.style(f"    → if false: skip → {next_str}", dim=True))
        elif step.is_loop:
            click.echo(click.style(f"    → LOOP: iterates over {step.loop_expr}", dim=True))
            if step.max_iterations:
                click.echo(click.style(f"    → max_iterations: {step.max_iterations}", dim=True))
            if step.next_steps:
                next_str = ", ".join(step.next_steps)
                click.echo(click.style(f"    → next: {next_str}", dim=True))
        else:
            click.echo(click.style("    → EXECUTES (unconditional)", fg="green"))
            if step.next_steps:
                next_str = ", ".join(step.next_steps)
                click.echo(click.style(f"    → next: {next_str}", dim=True))

        # On failure handler
        if step.on_failure:
            click.echo(click.style(f"    → on_failure: {step.on_failure}", dim=True))

        click.echo()

    # Summary
    click.echo("Summary:")
    click.echo(f"  {result.total_steps} steps total")
    if result.conditional_count:
        click.echo(f"  {result.conditional_count} conditional")
    if result.loop_count:
        click.echo(f"  {result.loop_count} loop")
    if result.has_finally:
        click.echo(click.style("  has finally block", fg="green"))
    click.echo()

    # Data dependencies
    if result.data_dependencies:
        click.echo("Data Dependencies:")
        for step_id, deps in result.data_dependencies.items():
            deps_str = ", ".join(f"{d}.outputs" for d in sorted(deps))
            click.echo(f"  {step_id} ← {deps_str}")
        click.echo()


# ─────────────────────────────────────────────────────────────────────────────
# CLI Group and Commands
# ─────────────────────────────────────────────────────────────────────────────


@click.group()
@click.version_option(package_name="burr_workflow")
def cli():
    """Workflow engine CLI for validation, analysis, and scaffolding."""
    pass


@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option("--strict", is_flag=True, help="Treat warnings as errors")
@click.option("-q", "--quiet", is_flag=True, help="Suppress output (only return exit code)")
@click.option(
    "--dry-run", "--analyze", "dry_run",
    is_flag=True,
    help="Show execution flow without running (static analysis)"
)
@click.option(
    "--inputs",
    type=str,
    default=None,
    help='JSON string of inputs for display (e.g., \'{"target": "example.com"}\')'
)
@click.option(
    "-v", "--visualize",
    type=click.Path(path_type=Path),
    default=None,
    is_flag=False,
    flag_value=Path("-"),
    help="Generate workflow diagram (default: mermaid to stdout)"
)
@click.option(
    "-e", "--engine",
    type=click.Choice(["mermaid", "graphviz"]),
    default="mermaid",
    help="Visualization engine"
)
@click.option(
    "-f", "--format",
    "output_format",
    type=click.Choice(["md", "png", "svg", "pdf", "dot"]),
    default="md",
    help="Output format"
)
@click.option("--show-conditions", is_flag=True, help="Include transition conditions in diagram")
def validate(
    workflow_file: Path,
    strict: bool,
    quiet: bool,
    dry_run: bool,
    inputs: Optional[str],
    visualize: Optional[Path],
    engine: str,
    output_format: str,
    show_conditions: bool,
):
    """Validate a workflow YAML file.

    \b
    Exit codes:
      0  Valid, no errors or warnings
      1  Validation errors found
      2  Warnings only (without --strict)

    \b
    Examples:
      workflow validate workflow.yaml
      workflow validate workflow.yaml --strict
      workflow validate workflow.yaml --dry-run
      workflow validate workflow.yaml -v  # Generate mermaid diagram
    """
    try:
        yaml_content = workflow_file.read_text(encoding="utf-8")
    except Exception as e:
        if not quiet:
            click.echo(f"Error reading file: {e}", err=True)
        sys.exit(1)

    # Handle dry-run/analyze mode
    if dry_run:
        try:
            yaml = ruamel.yaml.YAML()
            workflow = yaml.load(yaml_content)
        except Exception as e:
            click.echo(f"Error parsing YAML: {e}", err=True)
            sys.exit(1)

        # Still validate to catch errors
        result = validate_workflow_yaml(yaml_content, strict=strict)
        if not result.valid:
            print_result(result, str(workflow_file))
            sys.exit(1)

        # Run flow analysis
        analyzer = FlowAnalyzer(workflow)
        flow_result = analyzer.analyze()

        # Parse inputs if provided
        parsed_inputs = None
        if inputs:
            try:
                parsed_inputs = json.loads(inputs)
            except json.JSONDecodeError as e:
                click.echo(f"Error parsing --inputs JSON: {e}", err=True)
                sys.exit(1)

        print_flow_analysis(flow_result, parsed_inputs)
        sys.exit(0)

    # Handle visualization mode
    if visualize is not None:
        try:
            yaml = ruamel.yaml.YAML()
            workflow = yaml.load(yaml_content)
        except Exception as e:
            click.echo(f"Error parsing YAML: {e}", err=True)
            sys.exit(1)

        # Validate to catch errors
        result = validate_workflow_yaml(yaml_content, strict=strict)
        if not result.valid:
            print_result(result, str(workflow_file))
            sys.exit(1)

        # Compile to get the Burr Application
        try:
            from burr_workflow import WorkflowCompiler
            from burr_workflow.core.visualize import visualize as viz_func

            compiler = WorkflowCompiler()
            app = compiler.compile(workflow)

            # Determine output path
            output_path = None if str(visualize) == "-" else visualize

            # Generate visualization
            result_str = viz_func(
                app,
                output_path=output_path,
                engine=engine,
                format=output_format,
                include_conditions=show_conditions,
            )

            if str(visualize) == "-":
                click.echo(result_str)  # Mermaid to stdout
            elif not quiet:
                click.echo(f"Generated: {result_str}", err=True)

            sys.exit(0)

        except ImportError as e:
            if "graphviz" in str(e).lower():
                click.echo(
                    "Error: graphviz not installed. "
                    "Run: pip install burr_workflow[viz]",
                    err=True,
                )
            else:
                click.echo(f"Import error: {e}", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"Visualization error: {e}", err=True)
            sys.exit(1)

    # Standard validation
    try:
        result = validate_workflow_yaml(yaml_content, strict=strict)
    except Exception as e:
        if not quiet:
            click.echo(f"Validation failed: {e}", err=True)
        sys.exit(1)

    # Print results
    if not quiet:
        print_result(result, str(workflow_file))

    # Determine exit code
    if not result.valid:
        sys.exit(1)
    elif result.warnings and not strict:
        sys.exit(2)
    else:
        sys.exit(0)


@cli.command()
@click.argument("workflow_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--inputs",
    type=str,
    default=None,
    help='JSON string of inputs for display (e.g., \'{"target": "example.com"}\')'
)
@click.option("-q", "--quiet", is_flag=True, help="Suppress error messages")
def analyze(workflow_file: Path, inputs: Optional[str], quiet: bool):
    """Show execution flow (static analysis).

    This is equivalent to 'workflow validate --dry-run'.

    \b
    Examples:
      workflow analyze workflow.yaml
      workflow analyze workflow.yaml --inputs '{"target": "example.com"}'
    """
    try:
        yaml_content = workflow_file.read_text(encoding="utf-8")
    except Exception as e:
        if not quiet:
            click.echo(f"Error reading file: {e}", err=True)
        sys.exit(1)

    # Parse YAML
    try:
        yaml = ruamel.yaml.YAML()
        workflow = yaml.load(yaml_content)
    except Exception as e:
        click.echo(f"Error parsing YAML: {e}", err=True)
        sys.exit(1)

    # Validate first
    result = validate_workflow_yaml(yaml_content, strict=False)
    if not result.valid:
        print_result(result, str(workflow_file))
        sys.exit(1)

    # Run flow analysis
    analyzer = FlowAnalyzer(workflow)
    flow_result = analyzer.analyze()

    # Parse inputs if provided
    parsed_inputs = None
    if inputs:
        try:
            parsed_inputs = json.loads(inputs)
        except json.JSONDecodeError as e:
            click.echo(f"Error parsing --inputs JSON: {e}", err=True)
            sys.exit(1)

    print_flow_analysis(flow_result, parsed_inputs)
    sys.exit(0)


@cli.command()
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Output file path")
@click.option("--pretty", is_flag=True, help="Pretty-print with indentation")
@click.option("-q", "--quiet", is_flag=True, help="Suppress status messages")
def schema(output: Optional[Path], pretty: bool, quiet: bool):
    """Export JSON Schema for IDE validation.

    \b
    Examples:
      workflow schema                      # Print to stdout
      workflow schema -o schema.json       # Save to file
      workflow schema --pretty             # Pretty-print with indentation

    \b
    Usage in YAML files:
      Add this comment at the top of your workflow YAML:
      # yaml-language-server: $schema=./workflow-schema.json
    """
    try:
        # Generate schema from Pydantic model
        schema_dict = WorkflowDefinition.model_json_schema()

        # Add $schema meta reference and title
        schema_dict["$schema"] = "http://json-schema.org/draft-07/schema#"
        if "title" not in schema_dict:
            schema_dict["title"] = "Burr Workflow Definition"

        # Format output
        indent = 2 if pretty else None
        schema_json = json.dumps(schema_dict, indent=indent)

        if output:
            # Write to file
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(schema_json, encoding="utf-8")
            if not quiet:
                click.echo(f"Schema written to: {output}", err=True)
        else:
            # Print to stdout
            click.echo(schema_json)

        sys.exit(0)

    except Exception as e:
        if not quiet:
            click.echo(f"Error generating schema: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("name", required=False)
@click.option(
    "-t", "--template",
    type=click.Choice(list(TEMPLATES.keys())),
    default="minimal",
    help="Template to use"
)
@click.option(
    "-o", "--output-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("."),
    help="Output directory"
)
@click.option("--list-templates", "list_tmpls", is_flag=True, help="List available templates")
@click.option("-q", "--quiet", is_flag=True, help="Suppress status messages")
def create(
    name: Optional[str],
    template: str,
    output_dir: Path,
    list_tmpls: bool,
    quiet: bool,
):
    """Create a new workflow from template.

    \b
    Examples:
      workflow create my-workflow
      workflow create my-scan -o ./workflows/
      workflow create --list-templates
    """
    # Handle --list-templates
    if list_tmpls:
        click.echo("Available templates:\n")
        for tpl_name, desc in sorted(list_templates().items()):
            click.echo(f"  {tpl_name:12} - {desc}")
        click.echo("\nUsage: workflow create <name> --template=<template>")
        sys.exit(0)

    # Validate name is provided
    if not name:
        click.echo("Error: workflow name is required", err=True)
        click.echo("Usage: workflow create <name> [--template=<template>]", err=True)
        sys.exit(1)

    # Load and render template
    try:
        content = get_template(template, variables={"name": name})
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Determine output path
    # Sanitize name for filename: lowercase, replace spaces with hyphens
    filename = f"{name.replace(' ', '-').lower()}.yaml"
    output_path = output_dir / filename

    # Check if file exists
    if output_path.exists():
        click.echo(f"Error: File already exists: {output_path}", err=True)
        click.echo("Use a different name or delete the existing file.", err=True)
        sys.exit(1)

    # Ensure output directory exists
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        click.echo(f"Error creating directory: {e}", err=True)
        sys.exit(1)

    # Write file
    try:
        output_path.write_text(content, encoding="utf-8")
    except Exception as e:
        click.echo(f"Error writing file: {e}", err=True)
        sys.exit(1)

    if not quiet:
        click.echo(f"Created: {output_path}")
        click.echo(f"\nNext steps:")
        click.echo(f"  1. Edit {output_path} to customize your workflow")
        click.echo(f"  2. Validate: workflow validate {output_path}")
        click.echo(f"  3. Analyze:  workflow analyze {output_path}")

    sys.exit(0)


@cli.command("guard-init")
@click.option(
    "-s", "--scanner",
    "scanners",
    multiple=True,
    type=str,
    help="Specific scanners to initialize (can be used multiple times)"
)
@click.option("-q", "--quiet", is_flag=True, help="Suppress status messages")
def guard_init(scanners: tuple[str, ...], quiet: bool):
    """Pre-download LLM Guard models for offline use.

    Downloads ML models used by guardrails scanners. Run this before
    first workflow execution to avoid download delays.

    \b
    Scanners requiring model downloads:
      prompt_injection   - DeBERTa v2 model (~500MB)
      anonymize          - Presidio + spaCy models (~200MB)
      sensitive          - Same as anonymize
      factual_consistency - NLI model (~400MB)
      gibberish          - Language detection model
      no_refusal         - Refusal detection model
      relevance          - Semantic similarity model
      ban_topics         - Zero-shot classification model

    \b
    Examples:
      workflow guard-init                    # Download all common models
      workflow guard-init -s prompt_injection -s anonymize
    """
    # Check if llm-guard is installed
    try:
        from .guard import LLM_GUARD_AVAILABLE
        from .guard.scanner import initialize_models
    except ImportError:
        click.echo(
            "Error: llm-guard not installed.\n"
            "Run: pip install burr_workflow[guard]",
            err=True,
        )
        sys.exit(1)

    if not LLM_GUARD_AVAILABLE:
        click.echo(
            "Error: llm-guard not installed.\n"
            "Run: pip install burr_workflow[guard]",
            err=True,
        )
        sys.exit(1)

    # Default scanners to initialize (ML-based ones)
    default_scanners = [
        "prompt_injection",  # DeBERTa v2 (~500MB)
        "anonymize",         # Presidio + spaCy (~200MB)
        "factual_consistency",  # NLI model (~400MB)
    ]

    scanners_to_init = list(scanners) if scanners else default_scanners

    for scanner in scanners_to_init:
        if not quiet:
            click.echo(f"Initializing {scanner}...", nl=False)
        try:
            initialize_models(scanner)
            if not quiet:
                click.echo(click.style(" done", fg="green"))
        except Exception as e:
            if not quiet:
                click.echo(click.style(f" failed: {e}", fg="red"))

    if not quiet:
        click.echo("\nModel initialization complete.")

    sys.exit(0)


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
