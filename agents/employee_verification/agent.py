"""
Employee Verification Agent — ADK Agent built from YAML config.

The heavy lifting (prompt construction, tool loading, A2UI schema) happens
here, but all configurable values come from config/employee_verification.yaml.
"""

import os
import logging
from google.adk.agents import Agent
from a2ui.schema.manager import A2uiSchemaManager
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.common_modifiers import remove_strict_validation
from a2ui.schema.constants import VERSION_0_8

from agents._base.config_loader import load_agent_config, resolve_tool_functions

logger = logging.getLogger(__name__)

AGENT_CONFIG_NAME = "employee_verification"


def create_agent() -> Agent:
    """Create and return the Employee Verification Agent from config."""
    config = load_agent_config(AGENT_CONFIG_NAME)
    agent_cfg = config.get("agent", {})
    prompts = agent_cfg.get("prompts", {})

    # Resolve A2UI examples path
    a2ui_cfg = agent_cfg.get("a2ui", {})
    examples_dir = a2ui_cfg.get("examples_dir", "")
    # Make path absolute relative to the project root
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    examples_path = os.path.join(project_root, examples_dir)

    # Build A2UI system prompt
    schema_manager = A2uiSchemaManager(
        version=VERSION_0_8,
        catalogs=[
            BasicCatalog.get_config(
                version=VERSION_0_8,
                examples_path=examples_path,
            )
        ],
        schema_modifiers=[remove_strict_validation],
    )

    instruction = schema_manager.generate_system_prompt(
        role_description=prompts.get("role", ""),
        workflow_description=prompts.get("workflow", ""),
        ui_description=prompts.get("ui", ""),
        include_schema=True,
        include_examples=True,
        validate_examples=False,
    )

    # Resolve tools from config paths
    tool_paths = agent_cfg.get("tools", [])
    tools = resolve_tool_functions(tool_paths)

    # Model: agent-specific > default > env var > fallback
    model = agent_cfg.get("model", os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.5-flash"))

    agent = Agent(
        name=agent_cfg.get("name", "EmployeeVerificationAgent"),
        model=model,
        description=agent_cfg.get("description", ""),
        instruction=instruction,
        tools=tools,
    )

    logger.info(f"Created agent '{agent.name}' with model={model}, tools={len(tools)}")
    return agent


# Singleton pattern
_root_agent = None


def get_agent() -> Agent:
    """Get or create the singleton agent instance."""
    global _root_agent
    if _root_agent is None:
        _root_agent = create_agent()
    return _root_agent


root_agent = get_agent()
