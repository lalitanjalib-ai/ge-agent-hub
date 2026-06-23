#!/usr/bin/env python
"""
Agent Framework — Consolidated Debug and Diagnostics Tool

A comprehensive debugging tool that retrieves reasoning engines, fetches logs,
traces, diagnoses issues with Gemini Enterprise / Agent Engine deployments,
and provides local/remote A2A execution testing.

Usage:
    # List all reasoning engines in the project
    python scripts/debug_agent.py --list-engines

    # Get details and status of a specific agent's reasoning engine
    python scripts/debug_agent.py employee_verification

    # Fetch and print recent logs and traces (default 30 minutes)
    python scripts/debug_agent.py employee_verification

    # Tail/follow logs in real-time
    python scripts/debug_agent.py employee_verification --follow

    # Run a full diagnostic check on the agent's deployment and logs
    python scripts/debug_agent.py employee_verification --diagnose

    # Probe the remote Reasoning Engine API directly with an A2A message
    python scripts/debug_agent.py employee_verification --probe-remote "Find employee John Smith"

    # Run A2A execution locally to capture and print full tracebacks
    python scripts/debug_agent.py employee_verification --probe-local "Find employee John Smith"

    # Run A2A streaming execution locally to capture and print full tracebacks
    python scripts/debug_agent.py employee_verification --probe-local-stream "Find employee John Smith"

    # Probe remote Reasoning Engine streamQuery with multiple payload shapes
    python scripts/debug_agent.py employee_verification --probe-streamquery "Find employee John Smith"
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import sys
import time
import traceback
import urllib.parse
import urllib3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request
from google.cloud import logging as cloud_logging

# Disable warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PROJECT_ROOT.parent
for _path in (_PROJECT_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from agents._base.config_loader import list_available_agents, load_agent_config

_SSL_VERIFY: bool | str = True
_ssl_verify_env = os.environ.get("SSL_VERIFY", "").strip().lower()
if _ssl_verify_env in ("false", "0", "no"):
    _SSL_VERIFY = False
elif _ssl_verify_env:
    _SSL_VERIFY = _ssl_verify_env
elif os.environ.get("REQUESTS_CA_BUNDLE"):
    _SSL_VERIFY = os.environ.get("REQUESTS_CA_BUNDLE")

_LOG_STREAMS = {
    "all": None,
    "stdout": "aiplatform.googleapis.com/reasoning_engine_stdout",
    "stderr": "aiplatform.googleapis.com/reasoning_engine_stderr",
    "build": "aiplatform.googleapis.com/reasoning_engine_build",
}

_VERTEX_API_METHODS = (
    "StreamQueryReasoningEngine",
    "QueryReasoningEngine",
    "StreamReasoningEngine",
)

_DISCOVERY_API_METHODS = (
    "StreamAssist",
    "Assist",
)

def _err(msg: str) -> None:
    print(f"  [ERROR] {msg}")

def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")

def _get_bearer_token() -> str | None:
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        request = Request()
        credentials.refresh(request)
        return credentials.token
    except Exception as exc:
        _err(f"Authentication failed: {exc}")
        print("    Please run: gcloud auth application-default login")
        return None

def _get_de_hostname(ge_location: str) -> str:
    if ge_location == "global":
        return "discoveryengine.googleapis.com"
    return f"{ge_location}-discoveryengine.googleapis.com"

def _cloud_api_get(
    url: str,
    project_id: str,
    params: dict | None = None,
    *,
    quiet: bool = False,
) -> dict | list | None:
    token = _get_bearer_token()
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }
    response = requests.get(url, headers=headers, params=params or {}, verify=_SSL_VERIFY)
    if response.status_code != 200:
        if not quiet:
            _err(f"API request failed (HTTP {response.status_code})")
            print(f"    URL: {url}")
            print(f"    {response.text}")
        return None
    return response.json()

def _parse_engine_id(engine_ref: str) -> str:
    if "/" in engine_ref:
        return engine_ref.rstrip("/").split("/")[-1]
    return engine_ref

def _list_reasoning_engines(
    project_id: str,
    location: str,
    api_version: str = "v1beta1",
) -> list[dict]:
    url = (
        f"https://{location}-aiplatform.googleapis.com/{api_version}/"
        f"projects/{project_id}/locations/{location}/reasoningEngines"
    )

    engines: list[dict] = []
    page_token = None
    while True:
        params: dict = {"pageSize": 100}
        if page_token:
            params["pageToken"] = page_token

        data = _cloud_api_get(url, project_id, params)
        if not data:
            return engines

        engines.extend(data.get("reasoningEngines", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return engines

def _resolve_engine_for_agent(agent_name: str, project_id: str, location: str) -> dict | None:
    expected_display = f"{agent_name}_agent"
    matches = [
        engine
        for engine in _list_reasoning_engines(project_id, location)
        if engine.get("displayName") == expected_display
    ]

    if not matches:
        _err(f"No reasoning engine found with displayName='{expected_display}'")
        print("    Try: python scripts/debug_agent.py --list-engines")
        return None

    if len(matches) > 1:
        _warn(f"Found {len(matches)} engines named '{expected_display}' — using newest")
        for engine in matches[:5]:
            eid = _parse_engine_id(engine.get("name", ""))
            print(f"    - {eid}  created={engine.get('createTime', '?')}")

    matches.sort(key=lambda e: e.get("createTime", ""), reverse=True)
    return matches[0]

def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _runtime_filter(engine_id: str, log_stream: str = "all") -> str:
    parts = [
        'resource.type="aiplatform.googleapis.com/ReasoningEngine"',
        f'resource.labels.reasoning_engine_id="{engine_id}"',
    ]
    log_id = _LOG_STREAMS.get(log_stream)
    if log_id:
        parts.append(f'logName:"{log_id}"')
    return "(" + " AND ".join(parts) + ")"

def _vertex_api_filter(engine_id: str) -> str:
    method_match = " OR ".join(
        f'protoPayload.methodName:"{method}"' for method in _VERTEX_API_METHODS
    )
    return (
        f'(protoPayload.serviceName="aiplatform.googleapis.com" '
        f'AND protoPayload.resourceName:"{engine_id}" '
        f"AND ({method_match}))"
    )

def _discovery_engine_filter(ge_app_id: str | None) -> str:
    method_match = " OR ".join(
        f'protoPayload.methodName:"{method}"' for method in _DISCOVERY_API_METHODS
    )
    parts = [
        'protoPayload.serviceName="discoveryengine.googleapis.com"',
        f"({method_match})",
    ]
    if ge_app_id:
        parts.append(f'protoPayload.resourceName:"{ge_app_id}"')
    return "(" + " AND ".join(parts) + ")"

def _build_log_filter(
    engine_id: str,
    *,
    minutes: int,
    ge_app_id: str | None = None,
    log_source: str = "all",
    severities: list[str] | None = None,
    search: str | None = None,
    log_stream: str = "all",
) -> str:
    start_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    time_clause = f'timestamp>="{_iso_z(start_time)}"'

    runtime = _runtime_filter(engine_id, log_stream)
    vertex_api = _vertex_api_filter(engine_id)
    discovery = _discovery_engine_filter(ge_app_id)

    if log_source == "runtime":
        scope = runtime
    elif log_source == "vertex-api":
        scope = vertex_api
    elif log_source == "discovery-engine":
        scope = discovery
    else:
        scope = f"({runtime} OR {vertex_api} OR {discovery})"

    parts = [scope, time_clause]

    if severities:
        if len(severities) == 1:
            parts.append(f"severity={severities[0]}")
        else:
            joined = " OR ".join(f"severity={level}" for level in severities)
            parts.append(f"({joined})")

    if search:
        safe = search.replace('"', '\\"')
        parts.append(
            f'(textPayload:"{safe}" OR jsonPayload.message:"{safe}" '
            f'OR protoPayload.status.message:"{safe}")'
        )

    return "\n".join(parts)

def _classify_entry(entry) -> str:
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    resource_type = entry.resource.type if entry.resource else ""
    log_name = (entry.log_name or "").lower()

    if resource_type == "aiplatform.googleapis.com/ReasoningEngine":
        if "stderr" in log_name:
            return "runtime-stderr"
        if "stdout" in log_name:
            return "runtime-stdout"
        if "build" in log_name:
            return "runtime-build"
        return "runtime"

    service = payload.get("serviceName", "")
    method = payload.get("methodName", "")
    if service == "aiplatform.googleapis.com":
        return "vertex-api"
    if service == "discoveryengine.googleapis.com":
        return "discovery-engine"
    if method:
        return "audit"
    return "other"

def _extract_error_details(message: str) -> str | None:
    if "Error Details:" not in message:
        return None
    _, _, tail = message.partition("Error Details:")
    tail = tail.strip().lstrip(";").strip()
    if not tail:
        return None
    if tail.startswith("{"):
        try:
            return json.dumps(json.loads(tail), indent=2)
        except json.JSONDecodeError:
            return tail
    return tail

def _probe_remote_engine(project_id: str, location: str, engine_id: str, query: str, *, timeout: int = 30) -> None:
    token = _get_bearer_token()
    if not token:
        return

    base = (
        f"https://{location}-aiplatform.googleapis.com/v1beta1/"
        f"projects/{project_id}/locations/{location}/reasoningEngines/{engine_id}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }

    print("\n=== Probing Remote Engine ===")
    print(f"  Engine: {engine_id}")
    print(f"  Base:   {base}")
    print("-" * 80)

    try:
        card = requests.get(f"{base}/a2a/v1/card", headers=headers, timeout=timeout, verify=_SSL_VERIFY)
        print(f"  CARD {card.status_code}: {card.text[:500]}")
    except requests.Timeout:
        _err(f"CARD request timed out after {timeout}s")
        return
    except Exception as exc:
        _err(f"CARD request failed: {exc}")
        return

    payload = {
        "request": {
            "messageId": str(uuid.uuid4()),
            "contextId": str(uuid.uuid4()),
            "role": "ROLE_USER",
            "content": [{"text": query}],
        }
    }
    try:
        send = requests.post(
            f"{base}/a2a/v1/message:send",
            headers=headers,
            json=payload,
            timeout=timeout,
            verify=_SSL_VERIFY,
        )
        print(f"  SEND {send.status_code}:")
        print(f"  {send.text[:3000]}")
        details = _extract_error_details(send.text)
        if details:
            print("  Parsed error details:")
            print(details)
    except requests.Timeout:
        _warn(f"SEND timed out after {timeout}s — agent may be stuck during init")
    except Exception as exc:
        _err(f"SEND request failed: {exc}")
    print("-" * 80)
    print()

def _probe_remote_streamquery(project_id: str, location: str, engine_id: str, query: str, *, timeout: int = 90) -> None:
    token = _get_bearer_token()
    if not token:
        return

    base = (
        f"https://{location}-aiplatform.googleapis.com/v1beta1/"
        f"projects/{project_id}/locations/{location}/reasoningEngines/{engine_id}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }

    msg = {
        "messageId": str(uuid.uuid4()),
        "contextId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "content": [{"text": query}],
    }

    candidates = [
        {"class_method": "on_message_send_stream", "input": {"request": msg}},
        {"classMethod": "on_message_send_stream", "input": {"request": msg}},
        {"class_method": "on_message_send_stream", "input": {"request": msg, "context": {}}},
        {"class_method": "stream_query", "input": {"request": msg}},
    ]

    print("\n=== Probing Remote streamQuery ===")
    print(f"  Engine: {engine_id}")
    print(f"  Base:   {base}")
    print("-" * 80)

    for i, body in enumerate(candidates):
        print(f"CANDIDATE {i}: {json.dumps(body)[:120]}")
        try:
            r = requests.post(f"{base}:streamQuery?alt=sse", headers=headers, json=body,
                              timeout=timeout, stream=True, verify=_SSL_VERIFY)
            print("  HTTP Status:", r.status_code)
            n = 0
            for line in r.iter_lines(decode_unicode=True):
                if line:
                    n += 1
                    if n <= 10 or "error" in line.lower() or "state" in line.lower():
                        print("    ", line[:160])
            print(f"  Total lines received: {n}")
            if not n:
                print("    (no body received)")
        except Exception as e:
            print("  [ERROR]", type(e).__name__, str(e)[:200])
        print("-" * 40)
    print()

async def _probe_local_engine(agent_name: str, query: str, *, stream: bool = False) -> None:
    print(f"\n=== Probing Local Engine for '{agent_name}' (stream={stream}) ===")
    print(f"  Message: {query!r}")
    print("  Initializing local execution framework...")

    # Mirror server environment
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("PROJECT_ID", ""))
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("LOCATION", "us-central1"))

    try:
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.tasks import InMemoryTaskStore
        from a2a.types import Message, MessageSendParams, Role, Part, TextPart
        from a2a.utils import proto_utils
        from google.protobuf.json_format import MessageToJson
        
        # Dynamically import the agent's executor
        executor_module = importlib.import_module(f"agents.{agent_name}.executor")
        executor_class = None
        for attr_name in dir(executor_module):
            attr = getattr(executor_module, attr_name)
            if isinstance(attr, type) and attr_name.endswith("Executor") and attr_name != "BaseA2UIExecutor":
                executor_class = attr
                break
                
        if not executor_class:
            print(f"  [ERROR] Could not find executor class in agents.{agent_name}.executor")
            return
            
        executor = executor_class()
        handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=InMemoryTaskStore(),
        )

        message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.user,
            parts=[Part(root=TextPart(text=query))],
            context_id=str(uuid.uuid4()),
        )
        params = MessageSendParams(message=message)

        if stream:
            print("  Executing on_message_send_stream locally...")
            n = 0
            async for ev in handler.on_message_send_stream(params):
                n += 1
                root = getattr(ev, "root", ev)
                kind = type(root).__name__
                result = getattr(root, "result", root)
                state = None
                final = None
                has_form = False
                try:
                    status = getattr(result, "status", None)
                    if status is not None:
                        state = getattr(status, "state", None)
                        msg = getattr(status, "message", None)
                        if msg and getattr(msg, "parts", None):
                            has_form = any(
                                type(p.root).__name__ == "DataPart" for p in msg.parts
                            )
                    final = getattr(result, "final", None)
                except Exception as ie:
                    print("   (introspect err)", ie, file=sys.stderr)
                print(f"--- event #{n}: kind={kind} state={state} final={final} has_dataparts={has_form}")
                try:
                    proto = proto_utils.ToProto.stream_response(ev)
                    js = MessageToJson(proto)
                    print(f"    proto_ok bytes={len(js)}")
                except Exception as pe:
                    print(f"    !!! PROTO SERIALIZATION FAILED: {type(pe).__name__}: {pe}")
                    traceback.print_exc()
            print(f"\n=== STREAM ENDED after {n} events ===")
        else:
            print("  Executing on_message_send locally...")
            result = await handler.on_message_send(params)
            print("\n=== LOCAL RESULT ===")
            print(result)
    except Exception as exc:
        print("\n=== LOCAL EXCEPTION ===")
        print(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

def _audit_summary(payload: dict) -> str | None:
    if payload.get("@type") != "type.googleapis.com/google.cloud.audit.AuditLog":
        return None

    lines = []
    service = payload.get("serviceName", "?")
    method = payload.get("methodName", "?")
    short_method = method.rsplit(".", 1)[-1] if method else "?"
    lines.append(f"service={service}  method={short_method}")

    status = payload.get("status") or {}
    code = status.get("code")
    message = (status.get("message") or "").strip()
    if message:
        lines.append(f"status: code={code}  {message[:500]}")
        if "Error Details:" in message:
            detail_part = message.split("Error Details:", 1)[-1].strip()
            if detail_part.startswith("{") or detail_part.startswith('"'):
                try:
                    detail_json = json.loads(detail_part.strip("; "))
                    lines.append(f"errorDetails: {json.dumps(detail_json)[:800]}")
                except json.JSONDecodeError:
                    if detail_part:
                        lines.append(f"errorDetails: {detail_part[:800]}")

    for detail in payload.get("details") or []:
        if detail.get("@type", "").endswith("ErrorInfo"):
            reason = detail.get("reason")
            domain = detail.get("domain")
            if reason:
                lines.append(f"errorInfo: reason={reason} domain={domain}")

    auth = payload.get("authenticationInfo") or {}
    principal = auth.get("principalEmail") or auth.get("principalSubject")
    if principal:
        lines.append(f"caller: {principal}")

    return "\n".join(lines)

def _format_log_entry(entry, *, verbose: bool = False) -> str:
    timestamp = entry.timestamp.isoformat() if entry.timestamp else "?"
    severity = entry.severity or "DEFAULT"
    payload = entry.payload
    source = _classify_entry(entry)
    log_name = entry.log_name.rsplit("/", 1)[-1] if entry.log_name else "?"

    header = f"[{timestamp}] {severity} [{source}] ({log_name})"
    if entry.trace:
        trace_id = entry.trace.rsplit("/", 1)[-1]
        header += f" trace={trace_id}"

    if isinstance(payload, dict):
        audit = _audit_summary(payload)
        if audit and not verbose:
            return f"{header}\n{audit}"

    if isinstance(payload, dict):
        body = json.dumps(payload, indent=2, default=str)
    else:
        body = str(payload)

    if not verbose and len(body) > 2000:
        body = body[:2000] + "\n... (truncated, use --verbose for full payload)"

    return f"{header}\n{body}"

def _fetch_logs(
    project_id: str,
    engine_id: str,
    *,
    minutes: int,
    ge_app_id: str | None,
    log_source: str,
    limit: int,
    severities: list[str] | None,
    search: str | None,
    log_stream: str,
) -> list:
    client = cloud_logging.Client(project=project_id)
    log_filter = _build_log_filter(
        engine_id,
        minutes=minutes,
        ge_app_id=ge_app_id,
        log_source=log_source,
        severities=severities,
        search=search,
        log_stream=log_stream,
    )

    entries = client.list_entries(
        filter_=log_filter,
        order_by=cloud_logging.DESCENDING,
        page_size=min(limit, 1000),
        max_results=limit,
    )
    return list(entries)

def _count_by_source(entries: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        key = _classify_entry(entry)
        counts[key] = counts.get(key, 0) + 1
    return counts

def _print_diagnosis(entries: list, engine_id: str) -> None:
    if not entries:
        return

    errors = [e for e in entries if (e.severity or "").upper() == "ERROR"]
    by_source = _count_by_source(entries)

    print("  Diagnosis")
    print("-" * 80)
    print(f"  Entries by source: {by_source}")

    runtime_count = sum(v for k, v in by_source.items() if k.startswith("runtime"))
    vertex_errors = []
    discovery_errors = []

    for entry in errors:
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        service = payload.get("serviceName", "")
        method = payload.get("methodName", "")
        status = payload.get("status") or {}
        message = (status.get("message") or "").strip()

        if service == "aiplatform.googleapis.com":
            vertex_errors.append((method, message, status.get("code")))
        elif service == "discoveryengine.googleapis.com":
            discovery_errors.append((method, message, status.get("code")))

    if runtime_count == 0 and (vertex_errors or discovery_errors):
        print()
        _warn(
            "No runtime container logs found, but API audit logs exist. "
            "The agent likely fails during startup or before writing stdout/stderr."
        )
        print("    Check: agent import errors, A2UI examples path, IAM, redeploy.")

    if vertex_errors:
        print()
        print("  Vertex AI API errors (agent execution layer):")
        seen = set()
        for method, message, code in vertex_errors:
            key = (method, message[:200])
            if key in seen:
                continue
            seen.add(key)
            short = method.rsplit(".", 1)[-1] if method else "?"
            print(f"    - {short} (code={code})")
            if message:
                for line in message.splitlines()[:4]:
                    print(f"      {line}")

    if discovery_errors:
        print()
        print("  Gemini Enterprise errors (what the UI surfaces):")
        seen = set()
        for method, message, code in discovery_errors:
            key = (method, message[:200])
            if key in seen:
                continue
            seen.add(key)
            short = method.rsplit(".", 1)[-1] if method else "?"
            print(f"    - {short} (code={code})")
            if "REMOTE_AGENT_FAILURE" in message or "FAILED_PRECONDITION" in message:
                print("      -> GE could not get a response from Agent Engine.")
            if message:
                for line in message.splitlines()[:3]:
                    print(f"      {line}")

    tracebacks = [
        e for e in entries
        if isinstance(e.payload, str) and "Traceback" in e.payload
        or isinstance(e.payload, dict) and "Traceback" in json.dumps(e.payload, default=str)
    ]
    if tracebacks:
        print()
        print(f"  Found {len(tracebacks)} traceback log(s) — inspect runtime-stderr entries above.")

    print()
    print("  Useful follow-ups:")
    print(f"    python scripts/debug_agent.py --engine-id {engine_id} --log-source vertex-api --severity ERROR")
    print(f"    python scripts/debug_agent.py --engine-id {engine_id} --log-source runtime --log-stream stderr")
    print("    https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/troubleshooting/use")
    print("-" * 80)
    print()

def _print_logs(
    project_id: str,
    engine_id: str,
    *,
    minutes: int,
    ge_app_id: str | None,
    log_source: str,
    limit: int,
    severities: list[str] | None,
    search: str | None,
    log_stream: str,
    output_format: str,
    diagnose: bool,
    verbose: bool,
) -> int:
    try:
        log_filter = _build_log_filter(
            engine_id,
            minutes=minutes,
            ge_app_id=ge_app_id,
            log_source=log_source,
            severities=severities,
            search=search,
            log_stream=log_stream,
        )
        print(f"  Filter ({log_source}):")
        for line in log_filter.splitlines():
            print(f"    {line}")
        print()

        entries = _fetch_logs(
            project_id,
            engine_id,
            minutes=minutes,
            ge_app_id=ge_app_id,
            log_source=log_source,
            limit=limit,
            severities=severities,
            search=search,
            log_stream=log_stream,
        )
    except Exception as exc:
        _err(f"Failed to fetch logs: {exc}")
        print("    Ensure your account has roles/logging.viewer on the project.")
        return 0

    if not entries:
        print("  (no log entries found)")
        if log_source in ("all", "runtime"):
            print("    Tip: Failures often appear in API audit logs, not container stdout/stderr.")
            print("    Try: --log-source vertex-api --severity ERROR")
            print("    Try: --log-source discovery-engine --severity ERROR")
        return 0

    print(f"  Found {len(entries)} log entries")
    print("-" * 80)

    chronological = list(reversed(entries))
    if output_format == "json":
        payload = [
            {
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                "severity": entry.severity,
                "source": _classify_entry(entry),
                "log_name": entry.log_name,
                "trace": entry.trace,
                "payload": entry.payload,
            }
            for entry in chronological
        ]
        print(json.dumps(payload, indent=2, default=str))
    else:
        for entry in chronological:
            print(_format_log_entry(entry, verbose=verbose))
            print("-" * 80)

    if diagnose and output_format == "text":
        _print_diagnosis(entries, engine_id)

    return len(entries)

def _trace_duration_ms(trace: dict) -> int:
    spans = trace.get("spans") or []
    if not spans:
        return 0

    starts = [_parse_rfc3339(s["startTime"]) for s in spans if s.get("startTime")]
    ends = [_parse_rfc3339(s["endTime"]) for s in spans if s.get("endTime")]
    if not starts or not ends:
        return 0
    return int((max(ends) - min(starts)).total_seconds() * 1000)

def _parse_rfc3339(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)

def _fetch_traces(
    project_id: str,
    engine_id: str,
    *,
    minutes: int,
    limit: int,
) -> list[dict]:
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(minutes=minutes)
    url = f"https://cloudtrace.googleapis.com/v1/projects/{project_id}/traces"

    filters = [
        f"+reasoning_engine_id:{engine_id}",
        "span:/api/reasoning_engine",
        "span:/api/stream_reasoning_engine",
        "span:/api/bidi_reasoning_engine",
        None,
    ]

    for idx, trace_filter in enumerate(filters):
        params = {
            "startTime": _iso_z(start_time),
            "endTime": _iso_z(end_time),
            "pageSize": min(limit, 1000),
            "orderBy": "start desc",
            "view": "ROOTSPAN",
        }
        if trace_filter:
            params["filter"] = trace_filter

        quiet = idx < len(filters) - 1
        data = _cloud_api_get(url, project_id, params, quiet=quiet)
        if data is None:
            continue
        traces = data.get("traces", [])
        if traces:
            return traces[:limit]

    return []

def _print_traces(project_id: str, engine_id: str, *, minutes: int, limit: int) -> int:
    traces = _fetch_traces(project_id, engine_id, minutes=minutes, limit=limit)

    if not traces:
        print("  (no traces found)")
        print("    Tracing must be enabled on the agent deployment.")
        return 0

    print(f"  Found {len(traces)} traces")
    print("-" * 80)
    for trace in traces:
        trace_id = trace.get("traceId", "?")
        span_count = len(trace.get("spans") or [])
        duration_ms = _trace_duration_ms(trace)
        print(
            f"trace_id={trace_id}  spans={span_count}  duration={duration_ms}ms  "
            f"(detail: python scripts/debug_agent.py --trace-id {trace_id})"
        )
    return len(traces)

def _print_trace_detail(project_id: str, trace_id: str) -> None:
    url = f"https://cloudtrace.googleapis.com/v1/projects/{project_id}/traces/{trace_id}"
    data = _cloud_api_get(url, project_id)
    if not data:
        return

    spans = data.get("spans") or []
    if not spans:
        _err(f"No spans found for trace {trace_id}")
        return

    print(f"  Trace: projects/{project_id}/traces/{trace_id} ({len(spans)} spans)")
    print("-" * 80)

    def sort_key(span: dict) -> datetime:
        start = span.get("startTime")
        if not start:
            return datetime.min.replace(tzinfo=timezone.utc)
        return _parse_rfc3339(start)

    for span in sorted(spans, key=sort_key):
        start = span.get("startTime", "?")
        end = span.get("endTime", "?")
        duration_ms = 0
        if span.get("startTime") and span.get("endTime"):
            duration_ms = int(
                (_parse_rfc3339(span["endTime"]) - _parse_rfc3339(span["startTime"])).total_seconds() * 1000
            )

        print(f"[{start} -> {end}] ({duration_ms}ms) {span.get('name', '?')}")
        labels = span.get("labels") or {}
        if labels:
            print(f"  labels: {json.dumps(labels, default=str)}")
        print("-" * 80)

def _logs_explorer_url(
    project_id: str,
    engine_id: str,
    minutes: int,
    *,
    ge_app_id: str | None = None,
) -> str:
    runtime = _runtime_filter(engine_id)
    vertex_api = _vertex_api_filter(engine_id)
    discovery = _discovery_engine_filter(ge_app_id)
    query = f"({runtime} OR {vertex_api} OR {discovery})"
    encoded_query = urllib.parse.quote(query, safe="")
    duration = f"PT{minutes}M"
    return (
        "https://console.cloud.google.com/logs/query;"
        f"query={encoded_query};duration={duration}?project={project_id}"
    )

def _trace_console_url(project_id: str, trace_id: str | None = None) -> str:
    url = f"https://console.cloud.google.com/traces/list?project={project_id}"
    if trace_id:
        url += f"&tid={trace_id}"
    return url

def _print_engine_summary(engine: dict) -> str:
    name = engine.get("name", "")
    engine_id = _parse_engine_id(name)
    print(f"  Engine ID:    {engine_id}")
    print(f"  Display Name: {engine.get('displayName', '?')}")
    print(f"  Resource:     {name}")
    print(f"  Created:      {engine.get('createTime', '?')}")
    return engine_id

def _check_ge_registration(project_id: str, ge_location: str, app_id: str | None, agent_name: str, engine_id: str) -> None:
    print("Checking Gemini Enterprise registration status...")
    token = _get_bearer_token()
    if token and app_id:
        host = _get_de_hostname(ge_location)
        url = f"https://{host}/v1alpha/projects/{project_id}/locations/{ge_location}/collections/default_collection/engines/{app_id}/assistants/default_assistant/agents?pageSize=200"
        headers = {"Authorization": f"Bearer {token}", "X-Goog-User-Project": project_id}
        
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=15)
            if r.status_code == 200:
                ge_agents = r.json().get("agents", [])
                matching_ge = [ga for ga in ge_agents if agent_name in ga.get("displayName", "")]
                if matching_ge:
                    print(f"  [OK] Agent is registered in Gemini Enterprise:")
                    for ga in matching_ge:
                        print(f"    - Name: {ga.get('name')}")
                        print(f"    - State: {ga.get('state')}")
                        card = ga.get("a2aAgentDefinition", {}).get("jsonAgentCard", "")
                        if engine_id in card:
                            print("    - [OK] Card correctly references the active Reasoning Engine ID.")
                        else:
                            print("    - [ERROR] Card references a different Reasoning Engine ID!")
                else:
                    print("  [ERROR] Agent is NOT registered in Gemini Enterprise.")
            else:
                print(f"  [WARN] Could not query Gemini Enterprise agents (HTTP {r.status_code})")
        except Exception as e:
            print(f"  [WARN] Error querying Gemini Enterprise: {e}")
    print()

def _print_engine_details_full(engine: dict):
    print("=" * 60)
    print("  Reasoning Engine Details")
    print("=" * 60)
    print(f"  Name:         {engine.get('name')}")
    print(f"  Display Name: {engine.get('displayName')}")
    print(f"  Create Time:  {engine.get('createTime')}")
    print(f"  Update Time:  {engine.get('updateTime')}")
    
    spec = engine.get("spec", {})
    package_spec = spec.get("packageSpec", {})
    print(f"  Python Ver:   {package_spec.get('pythonVersion')}")
    print(f"  Pickle GCS:   {package_spec.get('pickleObjectGcsUri')}")
    
    env_vars = spec.get("deploymentSpec", {}).get("env", [])
    if env_vars:
        print("  Env Vars:")
        for ev in env_vars:
            print(f"    - {ev.get('name')} = {ev.get('value')}")
    print("=" * 60)

def main() -> None:
    load_dotenv(_PROJECT_ROOT / ".env", override=True)

    parser = argparse.ArgumentParser(
        description="View Cloud Logging, Cloud Trace, and run diagnostics for Agent Engine agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("agent", nargs="?", help="Agent name from config/<name>.yaml")
    parser.add_argument("--list-engines", action="store_true", help="List reasoning engines")
    parser.add_argument("--engine-id", help="Reasoning engine ID (skip agent name lookup)")
    parser.add_argument("--trace-id", help="Show span details for a Cloud Trace ID")
    parser.add_argument("--mode", choices=("logs", "traces", "both"), default="both")
    parser.add_argument("--minutes", type=int, default=30, help="Lookback window (default: 30)")
    parser.add_argument("--limit", type=int, default=50, help="Max entries/traces (default: 50)")
    parser.add_argument("--severity", action="append", help="Severity filter, e.g. --severity ERROR")
    parser.add_argument("--search", help="Free-text search in log payload")
    parser.add_argument(
        "--log-stream",
        choices=tuple(_LOG_STREAMS),
        default="all",
        help="Runtime log stream filter: stdout, stderr, build (default: all)",
    )
    parser.add_argument(
        "--log-source",
        choices=("all", "runtime", "vertex-api", "discovery-engine"),
        default="all",
        help="Log source (default: all — includes API audit logs)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--diagnose",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Print failure-pattern summary after logs (default: on)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full log payloads (default: compact audit summaries)",
    )
    parser.add_argument("--follow", action="store_true", help="Tail logs (Ctrl+C to stop)")
    parser.add_argument(
        "--probe-remote",
        type=str,
        metavar="QUERY",
        help="Probe the remote Reasoning Engine API with a query",
    )
    parser.add_argument(
        "--probe-local",
        type=str,
        metavar="QUERY",
        help="Run A2A execution locally to capture tracebacks",
    )
    parser.add_argument(
        "--probe-local-stream",
        type=str,
        metavar="QUERY",
        help="Run A2A streaming execution locally to capture tracebacks",
    )
    parser.add_argument(
        "--probe-streamquery",
        type=str,
        metavar="QUERY",
        help="Probe remote Reasoning Engine streamQuery with multiple payload shapes",
    )
    parser.add_argument("--region", help="Agent Engine region override")

    args = parser.parse_args()

    project_id = os.environ.get("PROJECT_ID")
    if not project_id:
        _err("PROJECT_ID is not set in .env")
        sys.exit(1)

    location = args.region or os.environ.get("LOCATION", "us-central1")
    ge_location = os.environ.get("GE_LOCATION", "us")
    ge_app_id = os.environ.get("GEMINI_ENTERPRISE_APP_ID")

    print()
    print("=" * 80)
    print("  Agent Framework — Consolidated Debug & Diagnostics")
    print(f"  Project: {project_id} | Region: {location}")
    print("=" * 80)
    print()

    if args.trace_id:
        print(f"  Cloud Trace: {_trace_console_url(project_id, args.trace_id)}")
        print()
        _print_trace_detail(project_id, args.trace_id)
        return

    if args.list_engines:
        engines = _list_reasoning_engines(project_id, location)
        if not engines:
            print("  No reasoning engines found.")
            return
        print(f"  Found {len(engines)} reasoning engine(s):\n")
        for engine in engines:
            _print_engine_summary(engine)
            print()
        return

    engine_id = args.engine_id
    agent_name = args.agent
    if agent_name:
        if agent_name not in list_available_agents():
            available = ", ".join(list_available_agents())
            _err(f"Unknown agent '{agent_name}'. Available: {available}")
            sys.exit(1)

        try:
            config = load_agent_config(agent_name)
            location = config.get("deploy", {}).get("region", location)
        except FileNotFoundError as exc:
            _err(str(exc))
            sys.exit(1)

        if not engine_id:
            print(f"  Resolving engine for agent '{agent_name}'...")
            engine = _resolve_engine_for_agent(agent_name, project_id, location)
            if not engine:
                sys.exit(1)
            engine_id = _print_engine_summary(engine)
            _print_engine_details_full(engine)
            print()
    elif not engine_id:
        if args.probe_local:
            # Local probe doesn't need remote engine resolution, but needs agent name
            parser.error("Provide an agent name for local probing")
        elif args.probe_local_stream:
            parser.error("Provide an agent name for local streaming probing")
        else:
            parser.error("Provide an agent name, --engine-id, --trace-id, or --list-engines")

    # Handle local probe
    if args.probe_local:
        asyncio.run(_probe_local_engine(agent_name, args.probe_local, stream=False))
        return

    if args.probe_local_stream:
        asyncio.run(_probe_local_engine(agent_name, args.probe_local_stream, stream=True))
        return

    # Handle remote probe
    if args.probe_remote:
        _probe_remote_engine(project_id, location, engine_id, args.probe_remote)
        return

    # Handle streamQuery probe
    if args.probe_streamquery:
        _probe_remote_streamquery(project_id, location, engine_id, args.probe_streamquery)
        return

    print(f"  Logs Explorer: {_logs_explorer_url(project_id, engine_id, args.minutes, ge_app_id=ge_app_id)}")
    print(f"  Cloud Trace:   {_trace_console_url(project_id)}")
    print()

    if args.follow:
        if args.mode not in ("logs", "both"):
            parser.error("--follow requires --mode logs or --mode both")

        seen: set[str] = set()
        print("  Following logs (Ctrl+C to stop)...")
        try:
            while True:
                entries = _fetch_logs(
                    project_id,
                    engine_id,
                    minutes=max(args.minutes, 5),
                    ge_app_id=ge_app_id,
                    log_source=args.log_source,
                    limit=args.limit,
                    severities=args.severity,
                    search=args.search,
                    log_stream=args.log_stream,
                )
                for entry in reversed(entries):
                    key = entry.insert_id or str(entry.timestamp)
                    if key in seen:
                        continue
                    seen.add(key)
                    print(_format_log_entry(entry, verbose=args.verbose))
                    print("-" * 80)
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n  Stopped.")
        return

    if args.mode in ("logs", "both"):
        print("  Cloud Logging")
        print("-" * 80)
        _print_logs(
            project_id,
            engine_id,
            minutes=args.minutes,
            ge_app_id=ge_app_id,
            log_source=args.log_source,
            limit=args.limit,
            severities=args.severity,
            search=args.search,
            log_stream=args.log_stream,
            output_format=args.format,
            diagnose=args.diagnose,
            verbose=args.verbose,
        )
        print()

    if args.mode in ("traces", "both"):
        print("  Cloud Trace")
        print("-" * 80)
        _print_traces(project_id, engine_id, minutes=args.minutes, limit=args.limit)

    if args.diagnose and agent_name:
        _check_ge_registration(project_id, ge_location, ge_app_id, agent_name, engine_id)

if __name__ == "__main__":
    main()
