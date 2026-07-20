import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=True)

project_id = os.environ["PROJECT_ID"]
engine_id = sys.argv[1] if len(sys.argv) > 1 else "4598736520231256064"

credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
credentials.refresh(Request())
headers = {"Authorization": f"Bearer {credentials.token}", "Content-Type": "application/json"}

log_filter = (
    'resource.type="aiplatform.googleapis.com/ReasoningEngine" AND '
    f'resource.labels.reasoning_engine_id="{engine_id}"'
)
payload = {
    "projectIds": [project_id],
    "filter": log_filter,
    "orderBy": "timestamp desc",
    "pageSize": 100,
}

r = requests.post(
    "https://logging.googleapis.com/v2/entries:list",
    headers=headers,
    json=payload,
    verify=False,
)
print("status", r.status_code)
for entry in r.json().get("entries", []):
    ts = entry.get("timestamp")
    sev = entry.get("severity", "DEFAULT")
    text = entry.get("textPayload") or json.dumps(entry.get("jsonPayload", {}))
    if any(k in text for k in ("Error", "Traceback", "Exception", "ERROR", "failed", "Failed")):
        print(f"[{ts}] {sev}: {text[:3000]}")
        print("---")
