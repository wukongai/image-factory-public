"""Adapter from semantic content extraction to Image Factory batch work.

Content Factory owns the meaning of an image slot.  This adapter deliberately
does not accept visual metaphor, action, expression, model, or provider
fields.  Those decisions are made later by the Image Factory IP director.
"""

from __future__ import annotations

from typing import Any

from image_factory import delivery_modes
from image_factory.errors import ImageFactoryError


CONTENT_SCHEMA_VERSION = "content-to-image-brief/v1"
IMAGE_BATCH_SCHEMA_VERSION = "image-batch/v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


def _outline(item: dict[str, Any]) -> dict[str, Any]:
    base = item.get("content") if isinstance(item.get("content"), dict) else {}
    value = item.get("content_outline")
    if isinstance(value, dict):
        return {**base, **value}
    return base


def _points(outline: dict[str, Any]) -> list[str]:
    points: list[str] = []
    for key in ("points", "flow", "evidence"):
        for value in _values(outline.get(key)):
            if value not in points:
                points.append(value)
    return points[:4]


def _to_inline_brief(
    item: dict[str, Any],
    index: int,
    total: int,
    selection_receipt: dict[str, Any],
) -> dict[str, Any]:
    outline = _outline(item)
    headline = _text(outline.get("headline") or outline.get("title") or item.get("title")) or f"图片要点 {index}"
    summary = _text(outline.get("summary") or outline.get("claim") or outline.get("business_relation"))
    inline: dict[str, Any] = {
        "schema_version": "image-asset-brief/v1",
        "id": _text(item.get("id")) or f"content-slot-{index:02d}",
        "scene": _text(item.get("scene")) or "ip_article_illustration",
        "goal": "explain_concept",
        "audience": _text(item.get("audience")) or "目标读者",
        "content": {
            "headline": headline,
            "subheadline": _text(outline.get("subheadline")),
            "summary": summary,
            "points": _points(outline),
            "cta": "",
            "brand": _text(outline.get("brand")),
        },
        "source": item.get("source") if isinstance(item.get("source"), dict) else {},
        "visual": {
            # These are semantic guardrails, not a visual direction.  Image
            # Factory carries them into its prompt/QA contract.
            "avoid": _values(outline.get("avoid"))[:8],
        },
        "director_context": {
            "sequence_index": index - 1,
            "sequence_size": total,
            "selection_owner": "image-factory",
        },
        "selection_receipt": dict(selection_receipt),
    }
    # Content Factory may identify an article/channel context, but it cannot
    # prescribe a visual template or shot.  Keep this metadata auditable.
    if isinstance(item.get("use_context"), dict):
        inline["source"]["use_context"] = dict(item["use_context"])
    return inline


def to_image_batch(request: dict[str, Any]) -> dict[str, Any]:
    """Convert a content-only request to the internal image-batch contract."""

    if _text(request.get("schema_version")) != CONTENT_SCHEMA_VERSION:
        raise ValueError(f"schema_version 必须是 {CONTENT_SCHEMA_VERSION}")
    raw_items = request.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("content-to-image-brief/v1 的 items 必须是非空数组")
    try:
        delivery_mode = delivery_modes.resolve(
            "ip_article_illustration",
            request.get("delivery_mode"),
            "",
        )
    except ImageFactoryError as exc:
        raise ValueError(str(exc)) from exc
    try:
        selection_receipt = delivery_modes.require_confirmed_selection(
            "ip_article_illustration",
            request,
        )
    except ImageFactoryError as exc:
        raise ValueError(str(exc)) from exc
    assert selection_receipt is not None
    converted_items = [
        {
            "id": _text(item.get("id")) or f"content-slot-{index:02d}",
            "title": _text(_outline(item).get("headline") or _outline(item).get("title") or item.get("title")),
            "inline_brief": _to_inline_brief(item, index, len(raw_items), selection_receipt),
        }
        if isinstance(item, dict)
        else item
        for index, item in enumerate(raw_items, 1)
    ]
    return {
        "schema_version": IMAGE_BATCH_SCHEMA_VERSION,
        "id": _text(request.get("id")) or "content-image-request",
        "defaults": {"delivery_mode": delivery_mode, "selection_receipt": selection_receipt},
        "items": converted_items,
        "source_contract": {
            "schema_version": CONTENT_SCHEMA_VERSION,
            "owner": "content-factory",
            "responsibility": "semantic content extraction only",
            "visual_direction_owner": "image-factory",
            "delivery_mode": delivery_mode,
        },
    }
