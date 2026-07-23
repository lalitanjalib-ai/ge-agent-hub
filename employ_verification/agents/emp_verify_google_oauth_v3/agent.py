"""
Employee Verification Google OAuth v3 Agent — direct Google OBO (no STS/WIF).
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

AGENT_CONFIG_NAME = "emp_verify_google_oauth_v3"


def create_agent() -> Agent:
    """Create and return the Employee Verification Google OAuth v3 agent."""
    config = load_agent_config(AGENT_CONFIG_NAME)
    agent_cfg = config.get("agent", {})
    prompts = agent_cfg.get("prompts", {})

    a2ui_cfg = agent_cfg.get("a2ui", {})
    examples_dir = a2ui_cfg.get("examples_dir", "")
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    examples_path = os.path.join(project_root, examples_dir)

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

    tool_paths = agent_cfg.get("tools", [])
    tools = resolve_tool_functions(tool_paths)

    model = agent_cfg.get("model", os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.5-flash"))

    agent = Agent(
        name=agent_cfg.get("name", "EmpVerifyGoogleOAuthV3Agent"),
        model=model,
        description=agent_cfg.get("description", ""),
        instruction=instruction,
        tools=tools,
    )

    logger.info(f"Created agent '{agent.name}' with model={model}, tools={len(tools)}")
    return agent


_root_agent = None


def get_agent() -> Agent:
    """Get or create the singleton agent instance."""
    global _root_agent
    if _root_agent is None:
        _root_agent = create_agent()
    return _root_agent


root_agent = get_agent()
