"""Jinja2 template loader and rendering.

Provides a single render() function for all templates.
"""

from jinja2 import PackageLoader, select_autoescape
from jinja2.sandbox import SandboxedEnvironment


# Create sandboxed Jinja2 environment with package loader
# SandboxedEnvironment prevents template code from accessing unsafe attributes
# or calling dangerous methods, providing defense-in-depth against SSTI attacks
_env = SandboxedEnvironment(
    loader=PackageLoader('llm_assistant', 'templates'),
    autoescape=select_autoescape(['html']),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(template_name: str, **kwargs) -> str:
    """Render a Jinja2 template with the given variables.

    Args:
        template_name: Path to template file (e.g., 'system_prompt.j2', 'prompts/watch_prompt.j2')
        **kwargs: Variables to pass to the template

    Returns:
        Rendered template string
    """
    template = _env.get_template(template_name)
    return template.render(**kwargs)
