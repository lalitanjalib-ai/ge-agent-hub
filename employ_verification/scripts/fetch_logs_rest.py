import os
import requests
import urllib3
import json
from google.auth import default
from google.auth.transport.requests import Request

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    project_id = "prj-us-bpg-spark-poc"
    engine_id = "279230323722551296"
    
    credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    token = credentials.token
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }
    
    # We want to search for logs related to this reasoning engine
    # Let's search in the last 2 hours
    log_filter = (
        f'resource.type="aiplatform.googleapis.com/ReasoningEngine" AND '
        f'resource.labels.reasoning_engine_id="{engine_id}"'
    )
    
    url = "https://logging.googleapis.com/v2/entries:list"
    payload = {
        "projectIds": [project_id],
        "filter": log_filter,
        "orderBy": "timestamp desc",
        "pageSize": 50
    }
    
    print("Fetching logs...")
    r = requests.post(url, headers=headers, json=payload, verify=False)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        entries = r.json().get("entries", [])
        print(f"Found {len(entries)} log entries:")
        for entry in entries:
            timestamp = entry.get("timestamp")
            severity = entry.get("severity", "DEFAULT")
            text_payload = entry.get("textPayload")
            json_payload = entry.get("jsonPayload")
            payload_str = text_payload or json.dumps(json_payload)
            print(f"[{timestamp}] {severity}: {payload_str}")
    else:
        print(r.text)

if __name__ == "__main__":
    main()
