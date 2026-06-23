#!/usr/bin/env python3
"""
Start adk web for local A2UI testing (codelab workflow).

Usage:
    cd poc
    python scripts/run_local.py

Reference:
    https://codelabs.developers.google.com/next26/adk-a2ui
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    poc_root = Path(__file__).resolve().parent.parent
    repo_root = poc_root.parent

    try:
        from dotenv import load_dotenv

        load_dotenv(poc_root / ".env", override=True)
    except ImportError:
        pass

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
    if not project:
        print("ERROR: Set GOOGLE_CLOUD_PROJECT in poc/.env")
        print("  cp .env.example .env")
        return 1

    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

    env = os.environ.copy()
    pythonpath_parts = [str(poc_root), str(repo_root)]
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath_parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    adk_cmd = shutil.which("adk")
    base_cmd = [adk_cmd] if adk_cmd else [sys.executable, "-m", "google.adk.cli"]
    # Run from poc/ with "." as the agent folder — avoids Windows path parsing
    # issues that can split "...\dn-innov-a2ui\poc" into extra CLI arguments.
    cmd = [
        *base_cmd,
        "web",
        ".",
        "--port",
        "8080",
        "--allow_origins",
        "*",
        "--reload_agents",
    ]

    print("=" * 72)
    print("  A2UI Local Dev (ADK web)")
    print(f"  Project: {project}")
    print(f"  Agent:   cloud_dashboard  (poc/)")
    print("  URL:     http://127.0.0.1:8080")
    print()
    print("  Sample prompts:")
    print("    - What's running in my project?")
    print("    - Does anything need my attention?")
    print("    - I need to deploy a new service")
    print("=" * 72)

    try:
        return subprocess.call(cmd, cwd=poc_root, env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
