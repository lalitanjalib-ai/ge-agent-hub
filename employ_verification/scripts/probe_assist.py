import os
import json
import re
import requests
import urllib3
from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request

# Disable warnings and load .env
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv(".env", override=True)

project_id = os.environ["PROJECT_ID"]
ge_location = os.environ.get("GE_LOCATION", "us")
app_id = os.environ["GEMINI_ENTERPRISE_APP_ID"]

print("================================================================")
print("  Gemini Enterprise streamAssist Prober")
print(f"  Project: {project_id} | Location: {ge_location} | App: {app_id}")
print("================================================================")

# 1. Get Google OAuth credentials
print("\n[1/4] Authenticating with Google Cloud...")
try:
    credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(Request())
    bearer_token = credentials.token
    print("  [OK] Authenticated successfully.")
except Exception as e:
    print(f"  [ERROR] Authentication failed: {e}")
    print("    Please run: gcloud auth application-default login")
    exit(1)

# 2. List GE agents to find "Employee Verification Agent"
print("\n[2/4] Resolving 'Employee Verification Agent' resource name from GE...")
de_hostname = f"{ge_location}-discoveryengine.googleapis.com" if ge_location != "global" else "discoveryengine.googleapis.com"
list_agents_url = (
    f"https://{de_hostname}/v1alpha/projects/{project_id}/"
    f"locations/{ge_location}/collections/default_collection/engines/{app_id}/"
    "assistants/default_assistant/agents?pageSize=200"
)

headers = {
    "Authorization": f"Bearer {bearer_token}",
    "Content-Type": "application/json",
    "X-Goog-User-Project": project_id,
}

agent_resource_name = None
try:
    response = requests.get(list_agents_url, headers=headers, verify=False, timeout=30)
    if response.status_code == 200:
        agents = response.json().get("agents", [])
        for agent in agents:
            display_name = agent.get("displayName", "")
            if "Employee Verification" in display_name:
                agent_resource_name = agent.get("name")
                print(f"  [OK] Found Agent: '{display_name}'")
                print(f"    Resource Name: {agent_resource_name}")
                break
        if not agent_resource_name:
            print("  [ERROR] Could not find 'Employee Verification Agent' in the registered list.")
            print("    Please ensure it is deployed and registered in Gemini Enterprise.")
            exit(1)
    else:
        print(f"  [ERROR] Failed to list agents (HTTP {response.status_code}): {response.text}")
        exit(1)
except Exception as e:
    print(f"  [ERROR] Error fetching agents: {e}")
    exit(1)

# 3. Formulate the query and body
print("\n[3/4] Preparing streamAssist payload...")
query_text = "Verify my employee record of John Smith"
print(f"  Query: '{query_text}'")

# Extract the short agent ID (the final segment of the resource name)
agent_id = agent_resource_name.split("/")[-1]

# Based on GE streamAssist protocol, to route to a specific agent,
# we must use agentsSpec with the agentId.
payload = {
    "query": {"text": query_text},
    "agentsSpec": {
        "agentSpecs": [
            {
                "agentId": agent_id
            }
        ]
    }
}

stream_assist_url = (
    f"https://{de_hostname}/v1alpha/projects/{project_id}/"
    f"locations/{ge_location}/collections/default_collection/engines/{app_id}/"
    "assistants/default_assistant:streamAssist"
)

# 4. Invoke streamAssist API and stream results
print("\n[4/4] Sending streamAssist request and streaming SSE events...")
try:
    r = requests.post(
        stream_assist_url,
        headers=headers,
        json=payload,
        verify=False,
        timeout=120,
        stream=True
    )
    print(f"  HTTP Status Code: {r.status_code}")
    if r.status_code != 200:
        print(f"  Error Response: {r.text}")
        exit(1)

    print("\n--- Streaming Response ---")
    line_count = 0
    raw_buffer = []
    
    for line in r.iter_lines(decode_unicode=True):
        if not line:
            continue
        line_count += 1
        raw_buffer.append(line)
        
        # Try to pretty print json/sse events
        safe_line = line.encode('ascii', errors='replace').decode('ascii')
        if line.startswith("data:"):
            sse_data = line[5:].strip()
            try:
                data_json = json.loads(sse_data)
                # Print any important text or state updates
                print(f"\n[Event {line_count}] (JSON Data):")
                js_str = json.dumps(data_json, indent=2)
                print(js_str.encode('ascii', errors='replace').decode('ascii'))
            except Exception:
                print(f"\n[Event {line_count}] (Raw Data): {safe_line}")
        else:
            print(f"[Line {line_count}] {safe_line[:120]}")
            
    print(f"\nStream finished. Received {line_count} lines.")
    
except Exception as e:
    print(f"  [ERROR] Connection/Streaming failed: {e}")
