"""Template registry and loader for workflow scaffolding.

Provides pre-built workflow templates for common use cases.
Templates support Jinja2 variable substitution for customization.
"""

import importlib.resources
from typing import Dict, Optional

from jinja2 import Template

# Template registry: name -> description
TEMPLATES: Dict[str, str] = {
    "minimal": "Bare minimum valid workflow",
    "osint": "OSINT reconnaissance workflow",
    "scan": "Port scanning with analysis",
    "credential": "Credential testing with loop break",
    "interactive": "Human-in-the-loop workflow",
    "api": "API integration with error handling",
}


def list_templates() -> Dict[str, str]:
    """Return dict of template_name -> description."""
    return TEMPLATES.copy()


def get_template(name: str, variables: Optional[Dict[str, str]] = None) -> str:
    """Load and optionally render template content by name.

    Args:
        name: Template name (minimal, osint, scan, credential, interactive, api)
        variables: Optional dict of variables to substitute in the template.
                  Common variables: {"name": "my-workflow"}

    Returns:
        Template YAML content as string (rendered if variables provided)

    Raises:
        ValueError: If template name is not recognized

    Example:
        # Raw template
        raw = get_template("minimal")

        # Rendered with workflow name
        rendered = get_template("minimal", {"name": "port-scanner"})
    """
    if name not in TEMPLATES:
        available = ", ".join(sorted(TEMPLATES.keys()))
        raise ValueError(f"Unknown template: {name}. Available: {available}")

    # Python 3.9+ way to read package data
    files = importlib.resources.files("burr_workflow.templates")
    template_content = (files / f"{name}.yaml").read_text(encoding="utf-8")

    # Render with variables if provided
    if variables:
        template = Template(template_content)
        return template.render(**variables)

    return template_content
