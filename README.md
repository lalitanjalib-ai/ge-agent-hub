# KPMG Agents

A config-driven multi-agent platform for deploying **A2UI**, **A2A**, and **Google ADK** agents to **Gemini Enterprise** via **Agent Engine**.

## Architecture

```
kpmg_agents/
├── pyproject.toml                        # Shared dependencies
├── .env / .env.example                   # Environment configuration
├── .gitignore
│
├── config/                               # Agent configurations (YAML)
│   ├── _defaults.yaml                    # Shared defaults (model, region, etc.)
│   └── employee_verification.yaml        # Agent-specific config
│
├── agents/                               # Agent definitions (one folder per agent)
│   ├── _base/                            # Shared base classes
│   │   ├── config_loader.py              # YAML config loader + merger
│   │   └── base_executor.py              # Generic A2A/A2UI executor
│   │
│   └── employee_verification/            # Employee Verification Agent
│       ├── agent.py                      # ADK Agent (reads from config YAML)
│       ├── executor.py                   # Thin executor subclass (3 lines)
│       └── examples/0.8/                 # A2UI JSON examples for this agent
│           ├── employee_list.json
│           ├── employee_verification_form.json
│           ├── action_confirmation.json
│           └── verification_success.json
│
├── tools/                                # Shared tool library
│   ├── registry.py                       # Tool metadata catalog
│   └── employee/                         # Tools grouped by domain
│       ├── lookup_employee.py
│       ├── update_employee_field.py
│       └── verify_employee.py
│
├── scripts/                              # Deploy + setup scripts
│   ├── deploy.py                         # Generic deploy CLI
│   ├── undeploy.py                       # Tear down agents
│   └── setup_bigquery.py                 # BigQuery table + mock data
│
└── data/                                 # Mock data, schemas, etc.
```

## Quick Start

### Prerequisites
- Google Cloud Project with Vertex AI and Gemini Enterprise enabled
- `gcloud` CLI authenticated (`gcloud auth application-default login`)
- Python 3.11+ with `uv` (recommended)

### 1. Install dependencies
```bash
cd kpmg_agents
uv sync
source .venv/bin/activate
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your project settings
```

### 3. Set up BigQuery (for employee verification agent)
```bash
python scripts/setup_bigquery.py
```

### 4. Deploy
```bash
# Deploy a single agent
python scripts/deploy.py employee_verification

# Deploy multiple agents
python scripts/deploy.py employee_verification benefits_enrollment

# Deploy ALL agents
python scripts/deploy.py --all
```

## Deploy CLI Reference

```bash
# Deploy a SINGLE agent
python scripts/deploy.py employee_verification

# Deploy MULTIPLE specific agents
python scripts/deploy.py employee_verification benefits_enrollment

# Deploy ALL agents (reads every YAML in config/)
python scripts/deploy.py --all

# Dry run (shows config without deploying)
python scripts/deploy.py employee_verification --dry-run

# List available agents
python scripts/deploy.py --list

# Undeploy an agent
python scripts/deploy.py employee_verification --undeploy
```

### Undeploy Script
```bash
# Undeploy a single agent
python scripts/undeploy.py employee_verification

# Undeploy all agents
python scripts/undeploy.py --all

# List agents registered in Gemini Enterprise
python scripts/undeploy.py --list
```

## Adding a New Agent

### 1. Create the config YAML
```bash
# config/my_new_agent.yaml
```
```yaml
agent:
  name: "MyNewAgent"
  display_name: "My New Agent"
  description: "What this agent does."
  # model: "gemini-2.5-pro"  # Override default model

  tools:
    - "tools.my_domain.my_tool.my_function"

  a2ui:
    examples_dir: "agents/my_new_agent/examples/0.8"

  prompts:
    role: "You are a helpful assistant that..."
    workflow: "Follow these steps..."
    ui: "Render A2UI components..."

  actions:
    my_button_action:
      template: "User clicked: {context_var}"

deploy:
  extra_requirements:
    - "some-extra-package>=1.0"
  extra_packages:
    - "agents/my_new_agent"
    - "tools"
  env_vars:
    NUM_WORKERS: "1"
  skills:
    - id: "my-skill"
      name: "My Skill"
      description: "What this skill does."
      examples: ["Example query 1", "Example query 2"]
```

### 2. Create the agent folder
```bash
mkdir -p agents/my_new_agent/examples/0.8
```

**agents/my_new_agent/agent.py** — Copy from employee_verification and change `AGENT_CONFIG_NAME`:
```python
from agents._base.config_loader import load_agent_config, resolve_tool_functions
# ... (same pattern as employee_verification/agent.py)
AGENT_CONFIG_NAME = "my_new_agent"
```

**agents/my_new_agent/executor.py** — Just 3 lines:
```python
from agents._base.base_executor import BaseA2UIExecutor

class MyNewAgentExecutor(BaseA2UIExecutor):
    AGENT_CONFIG_NAME = "my_new_agent"
```

### 3. Add tools (if needed)
```bash
mkdir -p tools/my_domain
```
Create tool functions, add to `tools/registry.py`, import in `tools/my_domain/__init__.py`.

### 4. Deploy
```bash
python scripts/deploy.py my_new_agent
```

That's it! No touching deploy code, no touching other agents.

## Adding New Tools

1. Create a new file in `tools/<domain>/` (e.g., `tools/benefits/lookup_benefits.py`)
2. Add metadata entry in `tools/registry.py`
3. Import in `tools/<domain>/__init__.py`
4. Add the dot-path to the agent's config YAML under `agent.tools`

## Tool Registry

The tool registry (`tools/registry.py`) provides metadata for management:
```python
from tools.registry import get_tools_by_tag, get_tool_metadata, list_all_tools

# Find all employee tools
employee_tools = get_tools_by_tag("employee")

# Find all read-only tools
read_tools = get_tools_by_operation("READ")

# Get metadata for a specific tool
meta = get_tool_metadata("lookup_employee")

# List all registered tools
all_tools = list_all_tools()
```

## Config System

- **`config/_defaults.yaml`** — Shared defaults inherited by all agents (model, region, base requirements)
- **`config/<agent_name>.yaml`** — Agent-specific config that overrides defaults
- Configs are deep-merged: agent values override defaults, nested dicts are merged recursively

## Key Design Decisions

| Aspect | How it works |
|--------|-------------|
| **Config** | YAML files in `config/`, merged with `_defaults.yaml` |
| **Tools vs Skills** | Tools = Python functions the LLM calls. Skills = metadata for A2A routing |
| **Executor** | Base class in `agents/_base/base_executor.py`, agents subclass with 3 lines |
| **Deploy** | Generic `scripts/deploy.py` reads config, imports executor dynamically |
| **A2UI** | Examples stored per-agent in `agents/<name>/examples/0.8/` |
