"""Business-owned reference selection and candidate compilation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from image_factory import reference_board, render_handoff
from image_factory.errors import ImageFactoryError


SCHEMA_VERSION = "image-reference-plan/v1"
STRATEGIES = {
    "primary_reference",
    "native_multi_reference",
    "reference_board",
    "candidate_handoffs",
}
DEFAULT_STRATEGY = "primary_reference"
DEFAULT_PURPOSE_ORDER = ("identity", "content", "composition", "style")


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else str(value or "").strip()


def _strategy_value(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"type": raw}
    if isinstance(raw, dict):
        return dict(raw)
    if raw is None:
        return {}
    raise ImageFactoryError("reference_strategy 必须是 string 或 object")


def resolve_strategy(template: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    visual = brief.get("visual") if isinstance(brief.get("visual"), dict) else {}
    if "reference_strategy" in visual:
        raw = visual.get("reference_strategy")
        source = "brief.visual.reference_strategy"
    elif "reference_strategy" in template:
        raw = template.get("reference_strategy")
        source = "template.reference_strategy"
    else:
        raw = {"type": DEFAULT_STRATEGY}
        source = "compiler_default"
    config = _strategy_value(raw)
    strategy_type = _as_text(config.get("type")) or DEFAULT_STRATEGY
    if strategy_type not in STRATEGIES:
        raise ImageFactoryError(
            f"reference_strategy.type 不支持: {strategy_type}; 可选: {', '.join(sorted(STRATEGIES))}"
        )
    purpose_order = config.get("purpose_order")
    if purpose_order is None:
        normalized_order = list(DEFAULT_PURPOSE_ORDER)
    elif isinstance(purpose_order, list) and all(_as_text(item) for item in purpose_order):
        normalized_order = [_as_text(item) for item in purpose_order]
    else:
        raise ImageFactoryError("reference_strategy.purpose_order 必须是非空字符串数组")
    return {
        "type": strategy_type,
        "source": source,
        "primary_asset": _as_text(config.get("primary_asset")),
        "purpose_order": normalized_order,
    }


def _handoff_asset(asset: dict[str, Any], *, required_default: bool = True) -> dict[str, Any]:
    return {
        "path": _as_text(asset.get("path")),
        "purpose": _as_text(asset.get("purpose")) or "content",
        "ownership": _as_text(asset.get("ownership")) or "authorized",
        "required": bool(asset.get("required", required_default)),
    }


def _primary_asset(assets: list[dict[str, Any]], strategy: dict[str, Any]) -> dict[str, Any]:
    explicit = strategy.get("primary_asset")
    if explicit:
        explicit_path = str(Path(explicit).expanduser().resolve())
        for asset in assets:
            if _as_text(asset.get("id")) == explicit or _as_text(asset.get("path")) == explicit:
                return asset
            if str(Path(_as_text(asset.get("path"))).expanduser().resolve()) == explicit_path:
                return asset
        raise ImageFactoryError(f"reference_strategy.primary_asset 未匹配授权素材: {explicit}")
    for purpose in strategy["purpose_order"]:
        for asset in assets:
            if _as_text(asset.get("purpose")) == purpose:
                return asset
    return assets[0]


def _candidate_id(item_id: str, asset: dict[str, Any], index: int) -> str:
    raw = _as_text(asset.get("id")) or Path(_as_text(asset.get("path"))).stem
    safe = "".join(char.lower() if char.isalnum() else "-" for char in raw).strip("-")
    safe = "-".join(part for part in safe.split("-") if part)[:48] or f"asset-{index:02d}"
    return f"{item_id}--candidate-{index:02d}-{safe}"


def _write_candidate_manifests(
    *,
    item_id: str,
    prompt_file: Path,
    size: str,
    output_dir: Path,
    assets: list[dict[str, Any]],
    source_manifest_name: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    root_manifest = output_dir / source_manifest_name
    for index, asset in enumerate(assets, 1):
        candidate_id = _candidate_id(item_id, asset, index)
        candidate_dir = output_dir / "candidates" / candidate_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        handoff = render_handoff.build_render_handoff(
            item_id=candidate_id,
            prompt_file=prompt_file,
            size=size,
            output_dir=candidate_dir / "image",
            assets=[_handoff_asset(asset)],
        )
        candidate_manifest_path = candidate_dir / "image-render-candidate.json"
        candidate_manifest = {
            "tool": "image-factory",
            "schema_version": "image-render-candidate/v1",
            "compile_only": True,
            "candidate_id": candidate_id,
            "source_manifest": str(root_manifest),
            "render_handoff": handoff,
            "visual_qa": {
                "required": True,
                "status": "pending",
                "attachment_evidence_is_visual_qa": False,
            },
        }
        candidate_manifest_path.write_text(
            json.dumps(candidate_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "asset": _handoff_asset(asset),
                "manifest_path": str(candidate_manifest_path),
                "render_handoff": handoff,
            }
        )
    return candidates


def compile_reference_plan(
    *,
    item_id: str,
    template: dict[str, Any],
    brief: dict[str, Any],
    authorized_assets: Iterable[dict[str, Any]],
    prompt_file: Path,
    size: str,
    output_dir: Path,
    source_manifest_name: str = "image-asset-manifest.json",
) -> dict[str, Any]:
    assets = [dict(asset) for asset in authorized_assets]
    strategy = resolve_strategy(template, brief)
    strategy_type = strategy["type"]
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "strategy": strategy_type,
        "strategy_source": strategy["source"],
        "authorized_asset_count": len(assets),
        "selected_asset_count": 0,
        "selected_assets": [],
        "selection_required": False,
    }

    if not assets:
        return plan

    if strategy_type == "primary_reference":
        selected = [_handoff_asset(_primary_asset(assets, strategy))]
    elif strategy_type == "native_multi_reference":
        selected = [_handoff_asset(asset) for asset in assets]
    elif strategy_type == "reference_board":
        board_asset, provenance, provenance_path = reference_board.compose_reference_board(
            item_id=item_id,
            assets=[_handoff_asset(asset) for asset in assets],
            output_dir=output_dir / "reference-board",
        )
        selected = [board_asset]
        plan["reference_board"] = {
            "path": board_asset["path"],
            "manifest_path": str(provenance_path),
            "sha256": provenance["board_sha256"],
            "source_count": len(provenance["sources"]),
            "qa": provenance["qa"],
        }
    else:
        candidates = _write_candidate_manifests(
            item_id=item_id,
            prompt_file=prompt_file,
            size=size,
            output_dir=output_dir,
            assets=assets,
            source_manifest_name=source_manifest_name,
        )
        plan.update(
            {
                "selected_asset_count": len(assets),
                "selected_assets": [_handoff_asset(asset) for asset in assets],
                "selection_required": True,
                "candidates": candidates,
            }
        )
        return plan

    plan["selected_asset_count"] = len(selected)
    plan["selected_assets"] = selected
    return plan
