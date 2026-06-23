"""Corporate SSL helpers for local Vertex AI calls (Windows / proxy CA)."""

from __future__ import annotations

import os
import ssl
from typing import Any


def is_ssl_verify_disabled() -> bool:
    value = os.environ.get("SSL_VERIFY", "").strip().lower()
    return value in ("false", "0", "no")


def _unverified_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def apply_ssl_env() -> bool:
    """Map .env SSL settings to env vars google-genai/httpx understand.

    google-genai reads SSL_CERT_FILE (not REQUESTS_CA_BUNDLE). When SSL_VERIFY
    is false, pass an explicit unverified SSLContext — google-genai treats
    verify=False as "unset" and replaces it with certifi's CA bundle.

    Returns True when SSL verification should be disabled.
    """
    if is_ssl_verify_disabled():
        _patch_corporate_ssl()
        return True

    ca_bundle = None
    ssl_verify_env = os.environ.get("SSL_VERIFY", "").strip()
    if ssl_verify_env and ssl_verify_env.lower() not in ("true", "1", "yes"):
        ca_bundle = ssl_verify_env
    elif os.environ.get("REQUESTS_CA_BUNDLE"):
        ca_bundle = os.environ["REQUESTS_CA_BUNDLE"]

    if ca_bundle:
        os.environ.setdefault("SSL_CERT_FILE", ca_bundle)

    return False


def _patch_corporate_ssl() -> None:
    """Patch google-genai and requests for corporate proxy TLS interception."""
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        pass

    try:
        import requests

        _orig_request = requests.Session.request

        def _request_no_verify(self, method, url, **kwargs):
            kwargs.setdefault("verify", False)
            return _orig_request(self, method, url, **kwargs)

        if not getattr(requests.Session.request, "_corporate_ssl_patched", False):
            _request_no_verify._corporate_ssl_patched = True  # type: ignore[attr-defined]
            requests.Session.request = _request_no_verify  # type: ignore[method-assign]
    except ImportError:
        pass

    try:
        from google.genai import _api_client

        if getattr(_api_client, "_corporate_ssl_patched", False):
            return

        ctx = _unverified_ssl_context()
        _orig_httpx = _api_client.BaseApiClient._ensure_httpx_ssl_ctx
        _orig_aiohttp = _api_client.BaseApiClient._ensure_aiohttp_ssl_ctx

        @staticmethod
        def _patched_httpx(options):
            sync_args, async_args = _orig_httpx(options)
            for args in (sync_args, async_args):
                args["verify"] = ctx
            return sync_args, async_args

        @staticmethod
        def _patched_aiohttp(options):
            args = _orig_aiohttp(options)
            args["ssl"] = ctx
            return args

        _api_client.BaseApiClient._ensure_httpx_ssl_ctx = _patched_httpx
        _api_client.BaseApiClient._ensure_aiohttp_ssl_ctx = _patched_aiohttp
        _api_client._corporate_ssl_patched = True
    except ImportError:
        pass


def gemini_http_options(disable_verify: bool) -> dict[str, Any] | None:
    if not disable_verify:
        return None

    # google-genai _ensure_httpx_ssl_ctx overwrites verify=False (falsy check).
    ctx = _unverified_ssl_context()
    return {
        "async_client_args": {"verify": ctx, "ssl": ctx},
        "client_args": {"verify": ctx},
    }
