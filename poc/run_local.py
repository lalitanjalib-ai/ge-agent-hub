#!/usr/bin/env python3
"""
Start adk web for local A2UI testing (codelab workflow).

Usage:
    cd poc
    python run_local.py

Reference:
    https://codelabs.developers.google.com/next26/adk-a2ui
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    poc_root = Path(__file__).resolve().parent
    repo_root = poc_root.parent

    try:
        from dotenv import load_dotenv

        load_dotenv(poc_root / ".env", override=True)
    except ImportError:
        pass

    sys.path.insert(0, str(poc_root))
    from ssl_config import apply_ssl_env, is_ssl_verify_disabled

    ssl_disabled = apply_ssl_env()
    if ssl_disabled:
        print("  NOTE: Stop any running server (Ctrl+C) and restart if SSL errors persist.")

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

    # ADK lists each subdirectory of agents_dir as an app. Run from the repo
    # root so the agent app is "poc" (poc/agent.py), not a stray "scripts" folder.
    cmd = [
        sys.executable,
        "-m",
        "google.adk.cli",
        "web",
        ".",
        "--port",
        "8080",
        "--allow_origins",
        "http://127.0.0.1:8080",
        "--allow_origins",
        "http://localhost:8080",
        "--reload_agents",
    ]

    dev_ui_url = "http://127.0.0.1:8080/dev-ui/?app=poc"

    print("=" * 72)
    print("  A2UI Local Dev (ADK web)")
    print(f"  Project: {project}")
    print("  Agent:   cloud_dashboard  (poc/agent.py)")
    print("  App:     poc  (select in dropdown, or use URL below)")
    if ssl_disabled or is_ssl_verify_disabled():
        print("  SSL:     verification disabled (SSL_VERIFY=false)")
    elif os.environ.get("SSL_CERT_FILE"):
        print(f"  SSL:     custom CA ({os.environ['SSL_CERT_FILE']})")
    print(f"  URL:     {dev_ui_url}")
    print()
    print("  Sample prompts:")
    print("    - What's running in my project?")
    print("    - Does anything need my attention?")
    print("    - I need to deploy a new service")
    print("=" * 72)

    try:
        return subprocess.call(cmd, cwd=repo_root, env=env, shell=False)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
