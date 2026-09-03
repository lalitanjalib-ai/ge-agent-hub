import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

import requests
from scripts.deploy import GEClient, _SSL_VERIFY

project_id = os.environ["PROJECT_ID"]
ge = GEClient(project_id, os.environ["GEMINI_ENTERPRISE_APP_ID"], os.environ.get("GE_LOCATION", "global"))
headers = ge._headers()
resp = requests.get(ge._agents_url(), headers=headers, params={"pageSize": 1000}, verify=_SSL_VERIFY)
print(f"HTTP {resp.status_code}")
for agent in resp.json().get("agents", []):
    aid = agent.get("name", "").split("/")[-1]
    auth_cfg = agent.get("authorizationConfig") or agent.get("authorization_config") or {}
    agent_auth = auth_cfg.get("agentAuthorization") or auth_cfg.get("agent_authorization")
    tool_auths = auth_cfg.get("toolAuthorizations") or auth_cfg.get("tool_authorizations") or []
    parts = []
    if agent_auth:
        parts.append(f"agentAuthorization={agent_auth}")
    if tool_auths:
        parts.append(f"toolAuthorizations={', '.join(tool_auths)}")
    auth = "; ".join(parts) if parts else "(none)"
    card_raw = agent.get("a2aAgentDefinition", {}).get("jsonAgentCard", "")
    url = json.loads(card_raw).get("url", "") if card_raw else ""
    print(f"- {agent.get('displayName')} [{aid}]")
    print(f"    auth: {auth}")
    if url:
        print(f"    url: {url}")
