"""Template registry and loader for workflow scaffolding.

Provides pre-built workflow templates for common use cases.
Templates use __PLACEHOLDER__ syntax for variable substitution to avoid
conflicts with workflow ${{ expression }} syntax.
"""

import importlib.resources

# Template registry: name -> description
# TODO: Add more templates (osint, scan, credential, interactive, api)
TEMPLATES: dict[str, str] = {
    "minimal": "Bare minimum valid workflow",
}


def list_templates() -> dict[str, str]:
    """Return dict of template_name -> description."""
    return TEMPLATES.copy()


def get_template(name: str, variables: dict[str, str] | None = None) -> str:
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

    # Simple string substitution using __PLACEHOLDER__ syntax
    # This avoids conflicts with workflow ${{ expression }} syntax
    if variables:
        for key, value in variables.items():
            placeholder = f"__{key.upper()}__"
            template_content = template_content.replace(placeholder, value)

    return template_content
