"""Deterministically compose authorized reference assets into one board."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

from image_factory.errors import ImageFactoryError


SCHEMA_VERSION = "reference-board/v1"
DEFAULT_SIZE = (2048, 2048)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_rgb(path: Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError) as exc:
        raise ImageFactoryError(f"参考板素材无法读取为图片: {path}: {exc}") from exc
    return image


def compose_reference_board(
    *,
    item_id: str,
    assets: Iterable[dict[str, Any]],
    output_dir: Path,
    size: tuple[int, int] = DEFAULT_SIZE,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Create a stable grid board and its provenance manifest.

    The board contains only caller-authorized assets. Labels are deterministic
    role/index annotations; no remote model is involved.
    """
    sources = [dict(asset) for asset in assets]
    if not sources:
        raise ImageFactoryError("reference_board 策略至少需要 1 张授权素材")

    width, height = size
    if width < 512 or height < 512:
        raise ImageFactoryError("reference board 画布宽高必须至少为 512")

    output_dir.mkdir(parents=True, exist_ok=True)
    board_path = output_dir / f"{item_id}-reference-board.png"
    manifest_path = output_dir / f"{item_id}-reference-board-manifest.json"

    count = len(sources)
    columns = max(1, math.ceil(math.sqrt(count)))
    rows = math.ceil(count / columns)
    outer = 48
    gap = 28
    label_height = 52
    cell_width = (width - outer * 2 - gap * (columns - 1)) // columns
    cell_height = (height - outer * 2 - gap * (rows - 1)) // rows
    image_height = cell_height - label_height
    if cell_width < 64 or image_height < 64:
        raise ImageFactoryError("reference board 素材过多，单元格不足 64px")

    board = Image.new("RGB", size, "#F4F1EA")
    draw = ImageDraw.Draw(board)
    font = ImageFont.load_default()
    source_records: list[dict[str, Any]] = []
    cells: list[dict[str, int]] = []

    for index, asset in enumerate(sources, 1):
        path = Path(str(asset.get("path") or "")).expanduser().resolve()
        if not path.is_file():
            raise ImageFactoryError(f"reference board 授权素材不存在: {path}")
        row = (index - 1) // columns
        column = (index - 1) % columns
        x = outer + column * (cell_width + gap)
        y = outer + row * (cell_height + gap)
        image = _open_rgb(path)
        fitted = ImageOps.contain(image, (cell_width, image_height), method=Image.Resampling.LANCZOS)
        image_x = x + (cell_width - fitted.width) // 2
        image_y = y + (image_height - fitted.height) // 2
        draw.rectangle((x, y, x + cell_width, y + image_height), fill="#FFFFFF", outline="#C9C3B8", width=2)
        board.paste(fitted, (image_x, image_y))
        purpose = str(asset.get("purpose") or "content")
        label = f"{index:02d}  {purpose}"
        draw.rectangle(
            (x, y + image_height, x + cell_width, y + cell_height),
            fill="#252525",
        )
        draw.text((x + 16, y + image_height + 17), label, fill="#FFFFFF", font=font)
        cells.append({"index": index, "x": x, "y": y, "w": cell_width, "h": cell_height})
        source_records.append(
            {
                "index": index,
                "path": str(path),
                "sha256": _sha256(path),
                "purpose": purpose,
                "ownership": str(asset.get("ownership") or "authorized"),
                "required": bool(asset.get("required", True)),
            }
        )

    board.save(board_path, format="PNG", optimize=True)
    provenance = {
        "schema_version": SCHEMA_VERSION,
        "id": item_id,
        "board_path": str(board_path),
        "board_sha256": _sha256(board_path),
        "size": f"{width}x{height}",
        "layout": {
            "type": "deterministic_grid",
            "columns": columns,
            "rows": rows,
            "outer_padding_px": outer,
            "gap_px": gap,
            "label_height_px": label_height,
            "cells": cells,
        },
        "annotation": "Each cell is labeled with stable index and business purpose.",
        "sources": source_records,
        "qa": {
            "status": "passed",
            "checks": ["all_sources_readable", "all_sources_traced", "board_written"],
        },
    }
    manifest_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    derivative_asset = {
        "path": str(board_path),
        "purpose": "content",
        "ownership": "derived_from_authorized_assets",
        "required": True,
    }
    return derivative_asset, provenance, manifest_path
