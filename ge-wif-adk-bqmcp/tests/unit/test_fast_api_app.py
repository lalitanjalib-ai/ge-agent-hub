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
"""Unit tests for the A2A auth shim in app.fast_api_app.

Covers `_AuthInjectingSessionService.create_session` only — the ContextVar
read path that turns an inbound bearer into ADK session state.
"""

from __future__ import annotations

import pytest

from app import fast_api_app


@pytest.fixture
def _clear_token():
    token_reset = fast_api_app._entra_token_var.set(None)
    try:
        yield
    finally:
        fast_api_app._entra_token_var.reset(token_reset)


@pytest.mark.asyncio
async def test_injects_token_when_state_empty(_clear_token) -> None:
    svc = fast_api_app._AuthInjectingSessionService(state_key="auth-1")
    fast_api_app._entra_token_var.set("jwt-abc")

    session = await svc.create_session(app_name="a", user_id="u", state={})

    assert session.state == {"auth-1": "jwt-abc"}


@pytest.mark.asyncio
async def test_injects_token_when_state_none(_clear_token) -> None:
    svc = fast_api_app._AuthInjectingSessionService(state_key="auth-1")
    fast_api_app._entra_token_var.set("jwt-abc")

    session = await svc.create_session(app_name="a", user_id="u", state=None)

    assert session.state == {"auth-1": "jwt-abc"}


@pytest.mark.asyncio
async def test_preserves_nonempty_caller_state(_clear_token) -> None:
    """Caller-supplied state takes precedence — we only fill the empty default."""
    svc = fast_api_app._AuthInjectingSessionService(state_key="auth-1")
    fast_api_app._entra_token_var.set("jwt-abc")

    session = await svc.create_session(
        app_name="a", user_id="u", state={"some-key": "some-val"}
    )

    assert session.state == {"some-key": "some-val"}


@pytest.mark.asyncio
async def test_no_token_passes_state_through(_clear_token) -> None:
    svc = fast_api_app._AuthInjectingSessionService(state_key="auth-1")
    # _entra_token_var stays None

    session = await svc.create_session(app_name="a", user_id="u", state={})

    assert session.state == {}
