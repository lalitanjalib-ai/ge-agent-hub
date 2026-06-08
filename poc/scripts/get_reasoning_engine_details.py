import os
import sys
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

import vertexai
from google.auth import default
from google.auth.transport.requests import Request
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    project_id = os.environ.get("PROJECT_ID")
    location = os.environ.get("LOCATION", "us-central1")
    engine_id = "6459435649870069760"
    
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        token = credentials.token
    except Exception as e:
        print(f"Error: {e}")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }

    url = f"https://{location}-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/{location}/reasoningEngines/{engine_id}"
    print(f"Fetching details for reasoning engine {engine_id} from {url}...")
    
    r = requests.get(url, headers=headers, verify=False)
    if r.status_code == 200:
        import json
        print(json.dumps(r.json(), indent=2))
    else:
        print(f"Error {r.status_code}: {r.text}")

if __name__ == "__main__":
    main()
