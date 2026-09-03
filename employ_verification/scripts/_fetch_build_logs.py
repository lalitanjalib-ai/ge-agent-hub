"""One-off: fetch latest Reasoning Engine build logs."""
import os
import ssl
import sys
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request

root = Path(__file__).resolve().parent.parent
load_dotenv(root / ".env", override=True)

if os.environ.get("SSL_VERIFY", "").strip().lower() == "false":
    ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore
    urllib3.disable_warnings()

project = os.environ["PROJECT_ID"]
creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds.refresh(Request())
headers = {"Authorization": f"Bearer {creds.token}"}

filt = (
    'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
    'AND logName:"reasoning_engine_build"'
)
body = {
    "resourceNames": [f"projects/{project}"],
    "filter": filt,
    "orderBy": "timestamp desc",
    "pageSize": 10,
}
resp = requests.post(
    "https://logging.googleapis.com/v2/entries:list",
    headers=headers,
    json=body,
    verify=False,
)
print("status:", resp.status_code)
if resp.status_code != 200:
    print(resp.text[:2000])
    sys.exit(1)

for entry in resp.json().get("entries", []):
    print("---", entry.get("timestamp"))
    payload = entry.get("textPayload")
    if not payload:
        payload = entry.get("jsonPayload", entry)
    print(str(payload)[:3000])
