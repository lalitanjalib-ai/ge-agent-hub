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
"""Set dummy env vars before `app/` imports run.

`app/agent.py` reads `GOOGLE_CLOUD_PROJECT` and `WIF_PROVIDER_RESOURCE` at
module import time and raises if either is missing. Tests that don't need
real values (most of them) get harmless dummies here; tests that DO need
real values override via `monkeypatch.setenv` before importing.
"""

import os
import subprocess


def _gcloud_default_project() -> str | None:
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        proj = out.stdout.strip()
        return proj or None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


# Use the gcloud-default project if available so live integration tests
# (which hit Vertex APIs via ADC) still resolve a real project; otherwise
# fall back to a dummy that lets unit tests import the app package cleanly.
os.environ.setdefault(
    "GOOGLE_CLOUD_PROJECT", _gcloud_default_project() or "test-project"
)
os.environ.setdefault(
    "WIF_PROVIDER_RESOURCE",
    "//iam.googleapis.com/locations/global/workforcePools/test-pool/providers/test-provider",
)
