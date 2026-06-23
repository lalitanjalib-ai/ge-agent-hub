import os
import requests
import urllib3
import json
import sys
from google.auth import default
from google.auth.transport.requests import Request

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    # Force stdout to use UTF-8 encoding to prevent Windows cp1252 errors
    if sys.stdout.encoding != 'utf-8':
        import io
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

    project_id = "prj-us-bpg-spark-poc"
    engine_id = "6459435649870069760"
    
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        token = credentials.token
    except Exception as e:
        print(f"Error getting credentials: {e}")
        return
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }
    
    # Let's search for logs from Cloud Run or Vertex AI Reasoning Engine execution
    log_filter = (
        f'resource.type="cloud_run_revision" OR '
        f'resource.type="aiplatform.googleapis.com/ReasoningEngine"'
    )
    
    url = "https://logging.googleapis.com/v2/entries:list"
    payload = {
        "projectIds": [project_id],
        "filter": log_filter,
        "orderBy": "timestamp desc",
        "pageSize": 50
    }
    
    print("Fetching Vertex / Cloud Run logs...")
    r = requests.post(url, headers=headers, json=payload, verify=False)
    if r.status_code == 200:
        entries = r.json().get("entries", [])
        print(f"Found {len(entries)} log entries:")
        for entry in entries:
            timestamp = entry.get("timestamp")
            log_name = entry.get("logName", "")
            resource = entry.get("resource", {})
            severity = entry.get("severity", "DEFAULT")
            text_payload = entry.get("textPayload")
            json_payload = entry.get("jsonPayload")
            payload_str = text_payload or json.dumps(json_payload)
            print(f"\n[{timestamp}] Log: {log_name} | Resource: {resource.get('type')} | Severity: {severity}")
            print(payload_str[:500])
    else:
        print(f"Error {r.status_code}: {r.text}")

if __name__ == "__main__":
    main()
