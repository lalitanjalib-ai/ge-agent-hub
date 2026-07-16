# ruff: noqa
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

from __future__ import annotations

import base64
import json
import time
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from app import entra_wif
from app.entra_wif import (
    MissingEntraTokenError,
    TokenExchangeError,
    _find_entra_token,
    _looks_like_entra_jwt,
    entra_wif_header_provider,
)


# --- helpers ------------------------------------------------------------------


def _make_jwt(claims: dict[str, Any]) -> str:
    """Build an unsigned-but-shape-valid JWT string with the given claims."""
    header = {"alg": "none", "typ": "JWT"}

    def b64(data: dict[str, Any]) -> str:
        return (
            base64.urlsafe_b64encode(json.dumps(data).encode())
            .rstrip(b"=")
            .decode()
        )

    return f"{b64(header)}.{b64(claims)}.sig"


def _entra_jwt(
    *, oid: str = "user-oid-1", sub: str = "user-sub-1", v: str = "2.0"
) -> str:
    iss = (
        "https://login.microsoftonline.com/tenant/v2.0"
        if v == "2.0"
        else "https://sts.windows.net/tenant/"
    )
    return _make_jwt({"iss": iss, "oid": oid, "sub": sub, "aud": "test-aud"})


def _fake_ctx(state: dict[str, Any]) -> SimpleNamespace:
    """Minimal stand-in for ReadonlyContext — just needs .state."""
    return SimpleNamespace(state=MappingProxyType(state))


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    entra_wif._reset_cache_for_tests()


class _FakeResponse:
    def __init__(self, status: int, body: dict[str, Any] | str):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self) -> dict[str, Any]:
        if isinstance(self._body, str):
            raise ValueError("non-json body")
        return self._body


def _patch_sts(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = "google-tok",
    expires_in: int = 3600,
    status: int = 200,
    body_override: dict[str, Any] | str | None = None,
) -> list[dict[str, Any]]:
    """Replace requests.post (as used by entra_wif) with a recording fake."""
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, data: dict[str, Any], timeout: float) -> _FakeResponse:
        calls.append({"url": url, "data": data, "timeout": timeout})
        if body_override is not None or status != 200:
            return _FakeResponse(status, body_override or {})
        return _FakeResponse(200, {"access_token": token, "expires_in": expires_in})

    monkeypatch.setattr(entra_wif.requests, "post", fake_post)
    return calls


# --- JWT detection ------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (_entra_jwt(v="2.0"), True),
        (_entra_jwt(v="1.0"), True),
        (_make_jwt({"iss": "https://accounts.google.com"}), False),
        (_make_jwt({"iss": ""}), False),
        ("not.a.jwt.too.many.parts", False),
        ("only.two", False),
        ("notajwt", False),
        ("", False),
        (None, False),
        (12345, False),
    ],
)
def test_looks_like_entra_jwt(value: object, expected: bool) -> None:
    assert _looks_like_entra_jwt(value) is expected


# --- state scanning -----------------------------------------------------------


def test_find_entra_token_finds_microsoft_jwt() -> None:
    jwt = _entra_jwt()
    state = {"some-key": jwt, "noise": "irrelevant"}
    assert _find_entra_token(state) == jwt


def test_find_entra_token_missing() -> None:
    assert _find_entra_token({}) is None
    assert _find_entra_token({"noise": "irrelevant"}) is None


# --- header provider end-to-end ----------------------------------------------


def test_header_provider_returns_bearer_and_user_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sts(monkeypatch, token="abc123")
    provider = entra_wif_header_provider(
        wif_provider="//iam.googleapis.com/locations/global/workforcePools/p/providers/pr",
        user_project="my-proj",
    )
    headers = provider(_fake_ctx({"auth-1": _entra_jwt()}))
    assert headers == {
        "Authorization": "Bearer abc123",
        "X-Goog-User-Project": "my-proj",
    }


def test_header_provider_caches_per_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_sts(monkeypatch, token="cached-tok")
    provider = entra_wif_header_provider(wif_provider="//w", user_project="p")
    ctx = _fake_ctx({"auth": _entra_jwt(oid="alice")})

    h1 = provider(ctx)
    h2 = provider(ctx)
    h3 = provider(ctx)

    assert h1 == h2 == h3
    assert len(calls) == 1, "Expected a single STS exchange across three calls"


def test_header_provider_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_sts(monkeypatch, token="t", expires_in=3600)
    provider = entra_wif_header_provider(wif_provider="//w", user_project="p")
    ctx = _fake_ctx({"auth": _entra_jwt(oid="alice")})

    # First call exchanges; second call (well within TTL) hits cache.
    provider(ctx)
    provider(ctx)
    assert len(calls) == 1

    # Advance time past expiry (minus refresh margin) -> miss, re-exchange.
    real_time = time.time
    monkeypatch.setattr(entra_wif.time, "time", lambda: real_time() + 3700)
    provider(ctx)
    assert len(calls) == 2


def test_header_provider_subjects_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_sts(monkeypatch)
    provider = entra_wif_header_provider(wif_provider="//w", user_project="p")

    provider(_fake_ctx({"auth": _entra_jwt(oid="alice")}))
    provider(_fake_ctx({"auth": _entra_jwt(oid="bob")}))
    provider(_fake_ctx({"auth": _entra_jwt(oid="alice")}))  # cache hit

    assert len(calls) == 2, "Each distinct subject should exchange once"


def test_header_provider_raises_when_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_sts(monkeypatch)
    provider = entra_wif_header_provider(wif_provider="//w", user_project="p")
    with pytest.raises(MissingEntraTokenError) as exc:
        provider(_fake_ctx({"unrelated": "value"}))

    assert "unrelated" in str(exc.value)
    assert calls == [], "STS must not be called when no token is present"


def test_header_provider_finds_jwt_under_any_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state-key GE picks is undocumented; the issuer-based scan must
    find the JWT regardless of which key it lands under."""
    _patch_sts(monkeypatch, token="tok")
    provider = entra_wif_header_provider(wif_provider="//w", user_project="p")
    headers = provider(_fake_ctx({"some-arbitrary-key": _entra_jwt()}))
    assert headers["Authorization"] == "Bearer tok"


def test_header_provider_raises_on_sts_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_sts(monkeypatch, status=400, body_override="invalid_subject_token")
    provider = entra_wif_header_provider(wif_provider="//w", user_project="p")
    with pytest.raises(TokenExchangeError) as exc:
        provider(_fake_ctx({"auth": _entra_jwt()}))
    assert "400" in str(exc.value)
    assert "invalid_subject_token" in str(exc.value)


def test_header_provider_sts_payload_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_sts(monkeypatch)
    provider = entra_wif_header_provider(
        wif_provider="//w/audience",
        user_project="my-proj",
        scope="custom-scope",
    )
    provider(_fake_ctx({"auth": _entra_jwt()}))

    assert len(calls) == 1
    data = calls[0]["data"]
    assert data["audience"] == "//w/audience"
    assert data["scope"] == "custom-scope"
    assert data["subject_token_type"] == "urn:ietf:params:oauth:token-type:jwt"
    assert data["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert json.loads(data["options"]) == {"userProject": "my-proj"}
