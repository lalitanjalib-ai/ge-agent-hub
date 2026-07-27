"""Build A2A agent cards with A2UI extensions from YAML config."""

from __future__ import annotations

import json
from typing import Any

from a2a.types import AgentSkill
from vertexai.preview.reasoning_engines.templates.a2a import create_agent_card

from agents._base.config_loader import load_agent_config


def build_a2ui_agent_card(
    agent_name: str,
    *,
    service_url: str | None = None,
    rpc_path: str = "/a2a/v1",
) -> dict[str, Any]:
    """Build the A2A agent card dict for GE registration or the Cloud Run server."""
    config = load_agent_config(agent_name)
    agent_cfg = config.get("agent", {})
    deploy_cfg = config.get("deploy", {})

    display_name = agent_cfg.get("display_name", agent_name)
    description = agent_cfg.get("description", "")
    skills_cfg = deploy_cfg.get("skills", [])

    skills = [
        AgentSkill(
            id=skill_def.get("id", ""),
            name=skill_def.get("name", ""),
            description=skill_def.get("description", ""),
            tags=skill_def.get("tags", []),
            examples=skill_def.get("examples", []),
        )
        for skill_def in skills_cfg
    ]

    input_modes = deploy_cfg.get("default_input_modes", ["text/plain"])
    output_modes = deploy_cfg.get("default_output_modes", ["text/plain"])

    agent_card = create_agent_card(
        agent_name=display_name,
        description=description,
        skills=skills,
        default_input_modes=input_modes,
        default_output_modes=output_modes,
    )

    if hasattr(agent_card, "model_dump"):
        card = agent_card.model_dump(by_alias=True, exclude_none=True)
    elif isinstance(agent_card, dict):
        card = agent_card
    else:
        card = json.loads(json.dumps(agent_card, default=str))

    if service_url:
        card["url"] = f"{service_url.rstrip('/')}{rpc_path}"

    a2ui_cfg = agent_cfg.get("a2ui", {})
    extension_uri = a2ui_cfg.get(
        "extension_uri", "https://a2ui.org/a2a-extension/a2ui/v0.8"
    )
    catalog_url = a2ui_cfg.get(
        "catalog_url",
        "https://a2ui.org/specification/v0_8/standard_catalog_definition.json",
    )
    card["capabilities"] = {
        "streaming": False,
        "extensions": [
            {
                "uri": extension_uri,
                "description": "Ability to render A2UI",
                "required": False,
                "params": {"supportedCatalogIds": [catalog_url]},
            }
        ],
    }
    return card
