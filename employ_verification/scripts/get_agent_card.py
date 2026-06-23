"""
KPMG Agents — Get A2A Agent Card from Deployed Agent Engine

Fetches the A2A agent card JSON from a deployed Reasoning Engine resource.
Also checks the agent's health/status before attempting to fetch the card.

Usage:
    # By resource name (full or just the ID)
    python scripts/get_agent_card.py projects/901535160018/locations/us-central1/reasoningEngines/1281446167556653056
    python scripts/get_agent_card.py 1281446167556653056

    # Save to file
    python scripts/get_agent_card.py 1281446167556653056 --output agent_card.json

    # List all deployed reasoning engines and their status
    python scripts/get_agent_card.py --list
"""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _get_bearer_token() -> str | None:
    """Gets a bearer token for authenticating with Google Cloud."""
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        request = Request()
        credentials.refresh(request)
        return credentials.token
    except Exception as e:
        print(f"Error getting credentials: {e}")
        print("Please run: gcloud auth application-default login")
        return None


def _get_headers(bearer_token: str) -> dict:
    """Build common request headers."""
    return {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }


def _build_resource_name(resource_id: str, project_id: str, location: str) -> str:
    """Build full resource name from an ID or return as-is if already full."""
    if resource_id.startswith("projects/"):
        return resource_id
    return f"projects/{project_id}/locations/{location}/reasoningEngines/{resource_id}"


def get_engine_details(
    resource_name: str,
    location: str,
    api_version: str = "v1beta1",
) -> dict | None:
    """Get the full details/status of a Reasoning Engine resource."""
    api_endpoint = f"{location}-aiplatform.googleapis.com"
    url = f"https://{api_endpoint}/{api_version}/{resource_name}"

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return None

    try:
        response = httpx.get(url, headers=_get_headers(bearer_token), timeout=30)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        print(f"  HTTP {e.response.status_code} fetching engine details")
        return None
    except Exception as e:
        print(f"  Error fetching engine details: {e}")
        return None


def list_engines(project_id: str, location: str, api_version: str = "v1beta1") -> list[dict]:
    """List all deployed Reasoning Engines in the project."""
    api_endpoint = f"{location}-aiplatform.googleapis.com"
    url = (
        f"https://{api_endpoint}/{api_version}/projects/{project_id}/"
        f"locations/{location}/reasoningEngines"
    )

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return []

    try:
        response = httpx.get(url, headers=_get_headers(bearer_token), timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("reasoningEngines", [])
    except Exception as e:
        print(f"Error listing engines: {e}")
        return []


def get_agent_card(
    resource_name: str,
    location: str,
    api_version: str = "v1beta1",
) -> dict | None:
    """Fetch the A2A agent card JSON from a deployed Reasoning Engine.

    Tries multiple known card endpoint paths.

    Args:
        resource_name: Full resource name (projects/.../reasoningEngines/...).
        location: GCP region (e.g. us-central1).
        api_version: API version (default: v1beta1).

    Returns:
        The agent card as a dict, or None on failure.
    """
    api_endpoint = f"{location}-aiplatform.googleapis.com"
    base_url = f"https://{api_endpoint}/{api_version}/{resource_name}"

    # Try multiple known card endpoint paths
    card_paths = [
        "/a2a/v1/card",
        "/a2a/.well-known/agent.json",
        "/.well-known/agent.json",
    ]

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return None

    headers = _get_headers(bearer_token)

    for path in card_paths:
        url = f"{base_url}{path}"
        try:
            response = httpx.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                print(f"  Found card at: {path}")
                return response.json()
            elif response.status_code == 404:
                continue  # Try next path
            else:
                print(f"  HTTP {response.status_code} at {path}")
        except Exception as e:
            print(f"  Error trying {path}: {e}")

    print(f"  No agent card found at any known endpoint.")
    return None


def main():
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

    parser = argparse.ArgumentParser(
        description="Fetch A2A agent card JSON from a deployed Agent Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/get_agent_card.py 1281446167556653056
  python scripts/get_agent_card.py projects/901535160018/locations/us-central1/reasoningEngines/1281446167556653056
  python scripts/get_agent_card.py 1281446167556653056 --output card.json
  python scripts/get_agent_card.py --list
        """,
    )

    parser.add_argument(
        "resource",
        nargs="?",
        help="Reasoning Engine resource name or ID",
    )
    parser.add_argument(
        "--output", "-o",
        help="Save the agent card to a JSON file instead of printing to stdout",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all deployed Reasoning Engines and exit",
    )
    parser.add_argument(
        "--location",
        default=os.environ.get("LOCATION", "us-central1"),
        help="GCP region (default: from LOCATION env var or us-central1)",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("PROJECT_ID"),
        help="GCP project ID (default: from PROJECT_ID env var)",
    )

    args = parser.parse_args()

    project_id = args.project
    location = args.location

    if not project_id:
        print("Error: PROJECT_ID not set. Use --project or set the PROJECT_ID env var.")
        sys.exit(1)

    # --list: show all deployed engines
    if args.list:
        print()
        print("=" * 80)
        print(f"  Deployed Reasoning Engines -- {project_id} / {location}")
        print("=" * 80)

        engines = list_engines(project_id, location)
        if not engines:
            print("  No Reasoning Engines found.")
        else:
            for engine in engines:
                name = engine.get("name", "?")
                display_name = engine.get("displayName", "?")
                create_time = engine.get("createTime", "?")
                update_time = engine.get("updateTime", "?")
                engine_id = name.split("/")[-1] if "/" in name else name
                print(f"  ID: {engine_id}")
                print(f"    Display Name: {display_name}")
                print(f"    Resource:     {name}")
                print(f"    Created:      {create_time}")
                print(f"    Updated:      {update_time}")
                print()

        print("=" * 80)
        print()
        print("To fetch the agent card, run:")
        print("  python scripts/get_agent_card.py <ENGINE_ID>")
        return

    # Fetch agent card
    if not args.resource:
        parser.print_help()
        sys.exit(1)

    resource_name = _build_resource_name(args.resource, project_id, location)

    print(f"Fetching A2A agent card from: {resource_name}")
    print()

    # Step 1: Check engine status
    print("[1/2] Checking engine status...")
    details = get_engine_details(resource_name, location)
    if details:
        display_name = details.get("displayName", "?")
        create_time = details.get("createTime", "?")
        update_time = details.get("updateTime", "?")
        print(f"  Display Name: {display_name}")
        print(f"  Created:      {create_time}")
        print(f"  Updated:      {update_time}")

        # Check for spec/deployment errors
        spec = details.get("spec", {})
        if spec:
            class_methods = spec.get("classMethods", [])
            if class_methods:
                print(f"  Class Methods: {class_methods}")

        # Print full details if verbose debugging needed
        # print(f"  Full details: {json.dumps(details, indent=2)}")
        print()
    else:
        print("  WARNING: Could not retrieve engine details.")
        print("  The engine may not exist or you may lack permissions.")
        print()

    # Step 2: Fetch the A2A agent card
    print("[2/2] Fetching A2A agent card...")
    card = get_agent_card(resource_name, location)
    if card is None:
        print()
        print("Failed to fetch agent card.")
        print()
        print("Possible reasons:")
        print("  1. The agent failed to start (check Cloud Logging for errors)")
        print("  2. The agent is still starting up (try again in a few minutes)")
        print("  3. The agent was not deployed as an A2A agent")
        print()
        print("To check logs, run:")
        engine_id = resource_name.split("/")[-1]
        print(f'  gcloud logging read "resource.labels.reasoning_engine_id={engine_id}" '
              f'--project={project_id} --limit=20 --format="table(timestamp,severity,textPayload)"')
        sys.exit(1)

    card_json = json.dumps(card, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(card_json, encoding="utf-8")
        print(f"\nAgent card saved to: {output_path}")
    else:
        print()
        print(card_json)


if __name__ == "__main__":
    main()
