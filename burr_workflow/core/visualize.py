"""Workflow graph visualization with mermaid and graphviz support.

This module provides visualization capabilities for compiled Burr workflows,
supporting both Mermaid (text-based, markdown-embeddable) and Graphviz
(high-quality images) output formats.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from burr.core import Application


def to_mermaid(
    app: "Application",
    include_conditions: bool = False,
) -> str:
    """Convert Burr graph to mermaid flowchart syntax.

    Args:
        app: Compiled Burr Application
        include_conditions: Include transition conditions as edge labels

    Returns:
        Mermaid flowchart syntax as a string
    """
    lines = ["flowchart TD"]
    graph = app.graph

    # Track node IDs for mermaid (must be valid identifiers)
    def make_id(name: str) -> str:
        """Convert action name to valid mermaid node ID."""
        return name.replace("-", "_").replace(".", "_").replace(" ", "_")

    # Add nodes
    for action in graph.actions:
        node_id = make_id(action.name)
        # Escape special characters in label
        label = action.name.replace('"', '\\"')
        lines.append(f'    {node_id}["{label}"]')

    # Add transitions
    for transition in graph.transitions:
        from_id = make_id(transition.from_.name)
        to_id = make_id(transition.to.name)

        if include_conditions and transition.condition:
            # Get condition name, truncate if too long
            cond_name = transition.condition.name
            if cond_name and cond_name != "default":
                cond = cond_name[:30]
                if len(cond_name) > 30:
                    cond += "..."
                # Escape pipe characters in condition
                cond = cond.replace("|", "\\|")
                lines.append(f"    {from_id} -->|{cond}| {to_id}")
            else:
                lines.append(f"    {from_id} --> {to_id}")
        else:
            lines.append(f"    {from_id} --> {to_id}")

    # Style entrypoint node (green background)
    if hasattr(graph, "entrypoint") and graph.entrypoint:
        start_id = make_id(graph.entrypoint.name)
        lines.append(f"    style {start_id} fill:#90EE90")

    return "\n".join(lines)


def visualize(
    app: "Application",
    output_path: Optional[Path] = None,
    engine: Literal["mermaid", "graphviz"] = "mermaid",
    format: Literal["md", "png", "svg", "pdf", "dot"] = "md",
    include_conditions: bool = False,
) -> str:
    """Generate workflow visualization.

    Args:
        app: Compiled Burr Application
        output_path: Output file path (None = return string for stdout)
        engine: "mermaid" (default, text-based) or "graphviz" (images)
        format: Output format:
            - mermaid: "md" (default, with code fence)
            - graphviz: "png", "svg", "pdf", "dot"
        include_conditions: Show transition conditions in diagram

    Returns:
        For mermaid: The mermaid syntax string
        For graphviz: The output file path as string

    Raises:
        ImportError: If graphviz is selected but not installed

    Examples:
        # Mermaid to stdout
        print(visualize(app))

        # Mermaid to file
        visualize(app, Path("workflow.md"))

        # Graphviz PNG
        visualize(app, Path("workflow.png"), engine="graphviz")
    """
    if engine == "mermaid":
        mermaid = to_mermaid(app, include_conditions)

        if output_path:
            # Wrap in markdown code fence for .md files
            if format == "md" or str(output_path).endswith(".md"):
                content = f"```mermaid\n{mermaid}\n```\n"
            else:
                content = mermaid + "\n"
            output_path.write_text(content, encoding="utf-8")
            return str(output_path)

        return mermaid

    else:  # graphviz
        # Use Burr's built-in visualization
        if format == "md":
            format = "png"  # Default for graphviz

        if output_path is None:
            raise ValueError("graphviz engine requires an output_path")

        # Ensure correct extension
        output_str = str(output_path)
        if not output_str.endswith(f".{format}"):
            output_path = Path(f"{output_str.rsplit('.', 1)[0]}.{format}")

        # Call Burr's visualize method
        app.graph.visualize(
            output_file_path=output_path,
            include_conditions=include_conditions,
        )

        return str(output_path)
