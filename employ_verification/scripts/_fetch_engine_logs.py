"""Fetch recent Reasoning Engine logs (one-off helper)."""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

engine_id = sys.argv[1] if len(sys.argv) > 1 else "4461978164456849408"
project = os.environ["PROJECT_ID"]

creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds.refresh(Request())

filter_q = (
    f'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
    f'AND resource.labels.reasoning_engine_id="{engine_id}"'
)
body = {
    "resourceNames": [f"projects/{project}"],
    "filter": filter_q,
    "orderBy": "timestamp desc",
    "pageSize": 25,
}
verify = False if os.environ.get("SSL_VERIFY", "").lower() == "false" else True
r = requests.post(
    "https://logging.googleapis.com/v2/entries:list",
    headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
    json=body,
    verify=verify,
    timeout=60,
)
print("status", r.status_code)
if r.status_code != 200:
    print(r.text[:1500])
    sys.exit(1)

for entry in r.json().get("entries", []):
    print("---")
    print(entry.get("timestamp"), entry.get("severity"))
    payload = entry.get("textPayload") or entry.get("jsonPayload")
    print(str(payload)[:2000])
