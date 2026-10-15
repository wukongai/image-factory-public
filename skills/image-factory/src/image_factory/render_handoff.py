"""Provider-neutral render handoff shared by every Image Factory business route."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "image-render-handoff/v1"


def build_render_handoff(
    *,
    item_id: str,
    prompt_file: Path,
    size: str,
    output_dir: Path,
    assets: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build the only render boundary emitted by business compilers."""
    normalized_assets: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        normalized_assets.append(
            {
                "path": str(asset.get("path") or ""),
                "purpose": str(asset.get("purpose") or "content"),
                "ownership": str(asset.get("ownership") or "authorized"),
                "required": bool(asset.get("required", True)),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "id": item_id,
        "prompt_file": str(prompt_file),
        "size": size,
        "output_dir": str(output_dir),
        "assets": normalized_assets,
    }
