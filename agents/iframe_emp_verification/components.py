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

"""
KPMG Iframe Audit Board — A2UI WebFrameUrl Component Builder.

Uses WebFrameUrl to load the Kanban board from a publicly hosted GCS URL.
Data is passed via base64-encoded URL query parameter (?data=<base64-json>).
This avoids embedding large HTML payloads inline (which caused GE timeouts).

A2UI sequence: surfaceUpdate → dataModelUpdate → beginRendering
"""

import base64
import json
import logging

logger = logging.getLogger(__name__)

_LAST_ISSUES_DATA = {}
SURFACE_ID = "kpmg-audit-board"

# Public GCS URL for the Kanban board HTML
KANBAN_HTML_URL = "https://storage.googleapis.com/kpmg_contract_leakage_demo_v4_meenu/kanban/index.html"


def build_kanban_board(surface_id: str, issues_data: dict) -> dict:
    """
    Build the A2UI v0.8 WebFrameUrl payload for the KPMG Kanban board.

    Encodes the issues data as a base64 URL parameter so the iframe can
    read it directly from the URL without needing postMessage.

    A2UI sequence: surfaceUpdate → dataModelUpdate → beginRendering
    """
    # Encode issues data as base64 URL parameter
    data_json = json.dumps(issues_data)
    data_b64 = base64.b64encode(data_json.encode("utf-8")).decode("utf-8")
    iframe_url = f"{KANBAN_HTML_URL}?data={data_b64}"

    surface_update = {
        "surfaceUpdate": {
            "surfaceId": surface_id,
            "components": [
                {
                    "id": "root",
                    "component": {
                        "WebFrameUrl": {
                            "url": {
                                "literalString": iframe_url
                            },
                            "height": 600
                        }
                    }
                }
            ]
        }
    }

    # Standard A2UI v0.8 dataModelUpdate (empty — data is in the URL)
    data_model_update = {
        "dataModelUpdate": {
            "surfaceId": surface_id,
            "contents": []
        }
    }

    begin_rendering = {
        "beginRendering": {
            "surfaceId": surface_id,
            "root": "root"
        }
    }

    return {
        "surface_update": surface_update,
        "data_model_update": data_model_update,
        "begin_rendering": begin_rendering
    }


def generate_kanban_a2ui_tool(issues_data: dict) -> str:
    """Generate the full <a2ui-json> block for the KPMG Iframe Audit Board.

    Uses WebFrameUrl to load the Kanban board from a public GCS URL with
    data encoded as a base64 query parameter. This avoids large inline HTML
    payloads that caused GE timeouts with the WebFrameSrcdoc approach.
    """
    global _LAST_ISSUES_DATA
    _LAST_ISSUES_DATA.clear()
    _LAST_ISSUES_DATA.update(issues_data)

    ui = build_kanban_board(SURFACE_ID, issues_data)

    # A2UI sequence: surfaceUpdate → dataModelUpdate → beginRendering
    sequence = [
        ui["surface_update"],
        ui["data_model_update"],
        ui["begin_rendering"]
    ]

    return f"<a2ui-json>\n{json.dumps(sequence, indent=2)}\n</a2ui-json>"


# Keep KanbanUIBuilder as a thin wrapper for backward compatibility
class KanbanUIBuilder:
    """Thin wrapper around build_kanban_board for backward compatibility."""

    @staticmethod
    def build_kanban_board(surface_id: str, issues_data: dict) -> dict:
        return build_kanban_board(surface_id, issues_data)
