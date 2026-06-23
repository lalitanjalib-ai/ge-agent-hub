import os
import requests
import urllib3
import json
from google.auth import default
from google.auth.transport.requests import Request

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    project_id = "prj-us-bpg-spark-poc"
    engine_id = "6459435649870069760"
    
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        token = credentials.token
        print("Successfully obtained Bearer token.")
    except Exception as e:
        print(f"Error getting credentials: {e}")
        return
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }
    
    # Let's call the A2A card endpoint first
    url = f"https://us-central1-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/us-central1/reasoningEngines/{engine_id}/a2a/v1/card"
    print(f"Calling A2A Card endpoint: {url}")
    r = requests.get(url, headers=headers, verify=False)
    print(f"Card Status: {r.status_code}")
    print(f"Card Response: {r.text[:500]}")
    
    # Let's call the A2A message send endpoint
    url_send = f"https://us-central1-aiplatform.googleapis.com/v1beta1/projects/{project_id}/locations/us-central1/reasoningEngines/{engine_id}/a2a/v1/message:send"
    print(f"\nCalling A2A Message Send endpoint: {url_send}")
    payload = {
        "message": "hi"
    }
    r_send = requests.post(url_send, headers=headers, json=payload, verify=False)
    print(f"Send Status: {r_send.status_code}")
    print(f"Send Response: {r_send.text[:500]}")

if __name__ == "__main__":
    main()
