# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""End-to-end test for the A2A FastAPI app.

Starts `app.fast_api_app:app` under uvicorn in a subprocess and exercises:
  * the dynamic agent-card endpoint
  * the A2A JSON-RPC `message/send` (non-streaming) path
  * the A2A JSON-RPC streaming path
  * the `/feedback` endpoint
  * JSON-RPC validation error handling

The agent's underlying tool call will fail without an Entra token
(MissingEntraTokenError surfaces from `entra_wif_header_provider`); this
test only asserts that the A2A transport layer works and that the failure
is reported through the protocol, not that the BigQuery MCP call succeeds.
"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from a2a.types import (
    JSONRPCErrorResponse,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    TextPart,
)
from requests.exceptions import RequestException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_URL = "http://127.0.0.1:8765"
A2A_RPC_URL = f"{BASE_URL}/a2a/app"
AGENT_CARD_URL = f"{A2A_RPC_URL}/.well-known/agent-card.json"
FEEDBACK_URL = f"{BASE_URL}/feedback"
HEALTHZ_URL = f"{BASE_URL}/healthz"

HEADERS = {"Content-Type": "application/json"}


def _log_output(pipe: Any, log_func: Any) -> None:
    for line in iter(pipe.readline, ""):
        log_func(line.strip())


def _start_server() -> subprocess.Popen[str]:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.fast_api_app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    ]
    env = os.environ.copy()
    env["INTEGRATION_TEST"] = "TRUE"
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    threading.Thread(
        target=_log_output, args=(process.stdout, logger.info), daemon=True
    ).start()
    threading.Thread(
        target=_log_output, args=(process.stderr, logger.error), daemon=True
    ).start()
    return process


def _wait_for_server(timeout: int = 90, interval: int = 1) -> bool:
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            response = requests.get(AGENT_CARD_URL, timeout=10)
            if response.status_code == 200:
                logger.info("Server is ready")
                return True
        except RequestException:
            pass
        time.sleep(interval)
    logger.error("Server did not become ready within %s seconds", timeout)
    return False


@pytest.fixture(scope="module")
def server_fixture() -> Iterator[subprocess.Popen[str]]:
    logger.info("Starting fast_api_app server process")
    server_process = _start_server()
    if not _wait_for_server():
        server_process.terminate()
        server_process.wait()
        pytest.fail("Server failed to start")
    try:
        yield server_process
    finally:
        logger.info("Stopping fast_api_app server process")
        server_process.terminate()
        server_process.wait()


def test_healthz(server_fixture: subprocess.Popen[str]) -> None:
    response = requests.get(HEALTHZ_URL, timeout=5)
    assert response.status_code == 200


def test_agent_card_served(server_fixture: subprocess.Popen[str]) -> None:
    response = requests.get(AGENT_CARD_URL, timeout=10)
    assert response.status_code == 200, response.text
    card = response.json()
    for field in ("name", "description", "skills", "capabilities", "url", "version"):
        assert field in card, f"agent card missing field: {field}"


def test_chat_non_streaming(server_fixture: subprocess.Popen[str]) -> None:
    """Send one A2A message and confirm the transport returns a Task.

    The agent's BQ MCP tool requires an Entra token; without one the agent
    will surface an error inside the task. We accept either a `completed`
    task or a `failed` task here — what we are asserting is that the A2A
    JSON-RPC envelope round-trips correctly.
    """
    message = Message(
        message_id=f"msg-user-{uuid.uuid4()}",
        role=Role.user,
        parts=[Part(root=TextPart(text="Hi!"))],
    )
    request = SendMessageRequest(
        id="test-req-002",
        params=MessageSendParams(message=message),
    )
    response = requests.post(
        A2A_RPC_URL,
        headers=HEADERS,
        json=request.model_dump(mode="json", exclude_none=True),
        timeout=120,
    )
    assert response.status_code == 200, response.text

    message_response = SendMessageResponse.model_validate(response.json())
    json_rpc_resp = message_response.root
    assert hasattr(json_rpc_resp, "result"), json_rpc_resp
    task = json_rpc_resp.result
    assert task.kind == "task"
    assert task.status.state in ("completed", "failed"), task.status.state


def test_chat_stream(server_fixture: subprocess.Popen[str]) -> None:
    """Stream an A2A message and confirm we receive a final status update."""
    message = Message(
        message_id=f"msg-user-{uuid.uuid4()}",
        role=Role.user,
        parts=[Part(root=TextPart(text="Hi!"))],
    )
    request = SendStreamingMessageRequest(
        id="test-req-001",
        params=MessageSendParams(message=message),
    )
    response = requests.post(
        A2A_RPC_URL,
        headers=HEADERS,
        json=request.model_dump(mode="json", exclude_none=True),
        stream=True,
        timeout=120,
    )
    assert response.status_code == 200, response.text

    responses: list[SendStreamingMessageResponse] = []
    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode("utf-8")
        if not line_str.startswith("data: "):
            continue
        json_data = json.loads(line_str[6:])
        responses.append(SendStreamingMessageResponse.model_validate(json_data))

    assert responses, "no streaming responses received"
    final_responses = [
        r.root
        for r in responses
        if hasattr(r.root, "result")
        and hasattr(r.root.result, "final")
        and r.root.result.final is True
    ]
    assert final_responses, "no final response in stream"
    final = final_responses[-1]
    assert final.result.kind == "status-update"
    assert final.result.status.state in ("completed", "failed")


def test_chat_stream_error_handling(server_fixture: subprocess.Popen[str]) -> None:
    """Invalid params should yield a JSON-RPC -32602 error."""
    invalid_data = {
        "jsonrpc": "2.0",
        "id": "test-error-001",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": f"msg-user-{uuid.uuid4()}",
            }
        },
    }
    response = requests.post(
        A2A_RPC_URL, headers=HEADERS, json=invalid_data, timeout=10
    )
    assert response.status_code == 200
    error_response = JSONRPCErrorResponse.model_validate(response.json())
    assert error_response.error.code == -32602


def test_collect_feedback(server_fixture: subprocess.Popen[str]) -> None:
    feedback_data = {
        "score": 4,
        "user_id": "test-user-456",
        "session_id": "test-session-456",
        "text": "Great response!",
    }
    response = requests.post(
        FEEDBACK_URL, json=feedback_data, headers=HEADERS, timeout=10
    )
    assert response.status_code == 200
