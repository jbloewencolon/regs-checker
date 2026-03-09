"""Prompt template loader — loads versioned YAML prompt templates with Jinja2 rendering.

Supports both externalized YAML templates (prompts/ directory) and inline fallbacks.
Templates are cached after first load for performance.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, BaseLoader

import structlog

logger = structlog.get_logger()

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

_jinja_env = Environment(loader=BaseLoader(), keep_trailing_newline=True)


@lru_cache(maxsize=16)
def load_prompt_template(agent_name: str) -> dict[str, Any] | None:
    """Load a prompt template YAML file for the given agent.

    Returns None if no template file exists (agent uses inline prompts).
    """
    filepath = PROMPTS_DIR / f"{agent_name}.yml"
    if not filepath.exists():
        return None

    with open(filepath) as f:
        template = yaml.safe_load(f)

    logger.debug("prompt_template_loaded", agent=agent_name, version=template.get("version"))
    return template


def render_prompt(template_str: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template string with the given context."""
    tmpl = _jinja_env.from_string(template_str)
    return tmpl.render(**context).strip()


def get_template_version(agent_name: str) -> str | None:
    """Return the version string from the agent's prompt template, or None."""
    template = load_prompt_template(agent_name)
    if template:
        return template.get("version")
    return None
