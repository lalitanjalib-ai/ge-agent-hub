"""
KPMG widgets catalog provider for A2uiSchemaManager.

Merges the A2UI standard catalog with KPMG-branded example payloads so agents
learn consistent UI patterns for Gemini Enterprise.
"""

from __future__ import annotations

import os
from pathlib import Path

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.constants import VERSION_0_8

WIDGETS_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = WIDGETS_ROOT / "examples" / "0.8"
CATALOG_PATH = WIDGETS_ROOT / "catalog" / "kpmg_catalog_definition.json"

# Stable catalog ID advertised to A2A clients during catalog negotiation.
KPMG_CATALOG_ID = "https://kpmg.internal/a2ui/v0_8/kpmg_widgets_catalog_definition.json"


class KpmgWidgetsCatalog:
    """Provider that extends BasicCatalog with KPMG widget examples."""

    @staticmethod
    def get_examples_path(version: str = VERSION_0_8) -> Path:
        """Return the KPMG widget examples directory for the given A2UI version."""
        path = WIDGETS_ROOT / "examples" / version.replace(".", "_")
        if path.exists():
            return path
        return EXAMPLES_DIR

    @staticmethod
    def get_catalog_path() -> Path:
        """Return path to the KPMG catalog schema JSON."""
        return CATALOG_PATH

    @classmethod
    def get_config(
        cls,
        version: str = VERSION_0_8,
        agent_examples_path: str | os.PathLike[str] | None = None,
        include_kpmg_examples: bool = True,
    ) -> dict:
        """
        Build an A2uiSchemaManager catalog config.

        Args:
            version: A2UI protocol version (default 0.8).
            agent_examples_path: Optional agent-specific examples directory.
            include_kpmg_examples: When True, merge KPMG widget examples into
                the catalog examples path passed to BasicCatalog.

        Returns:
            Catalog config dict compatible with A2uiSchemaManager.
        """
        examples_paths: list[str] = []

        if include_kpmg_examples:
            examples_paths.append(str(cls.get_examples_path(version)))

        if agent_examples_path:
            examples_paths.append(str(agent_examples_path))

        # BasicCatalog accepts a single examples_path; use merged temp dir logic
        # via the first path when only one, or agent path with KPMG as secondary
        # by passing a comma-joined parent — SDK expects one dir, so prefer a
        # combined approach: agent examples take precedence, KPMG supplements.
        primary_examples = examples_paths[0] if examples_paths else str(EXAMPLES_DIR)
        if len(examples_paths) > 1:
            primary_examples = _merge_example_dirs(examples_paths)

        return BasicCatalog.get_config(
            version=version,
            examples_path=primary_examples,
        )

    @classmethod
    def get_catalogs_for_agent(
        cls,
        version: str = VERSION_0_8,
        agent_examples_path: str | os.PathLike[str] | None = None,
    ) -> list[dict]:
        """
        Return catalog list for A2uiSchemaManager — standard + KPMG examples.

        Usage in agent.py::

            schema_manager = A2uiSchemaManager(
                version=VERSION_0_8,
                catalogs=KpmgWidgetsCatalog.get_catalogs_for_agent(
                    agent_examples_path=examples_path,
                ),
                schema_modifiers=[remove_strict_validation],
            )
        """
        return [
            cls.get_config(
                version=version,
                agent_examples_path=agent_examples_path,
                include_kpmg_examples=True,
            )
        ]


def _merge_example_dirs(paths: list[str]) -> str:
    """
    Return a directory containing example JSON from all paths.

    Creates a temporary merged directory when multiple example sources exist.
    Agent examples overwrite KPMG examples on name collision.
    """
    import shutil
    import tempfile

    merged = Path(tempfile.mkdtemp(prefix="kpmg_a2ui_examples_"))
    for path in reversed(paths):
        source = Path(path)
        if not source.is_dir():
            continue
        for json_file in source.glob("*.json"):
            shutil.copy2(json_file, merged / json_file.name)
    return str(merged)
