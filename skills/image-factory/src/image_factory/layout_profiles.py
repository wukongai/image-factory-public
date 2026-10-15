"""Orthogonal layout-profile registry for deterministic mixed layouts."""

from __future__ import annotations

import copy
from typing import Any

from image_factory.errors import ImageFactoryError


LAYOUT_PROFILES: dict[str, dict[str, Any]] = {
    "safe-split": {
        "display_name": "稳妥分离式",
        "aliases": ["分离式", "稳妥排版", "safe", "safe-split"],
        "renderer": "rounded-card",
        "default": True,
    },
    "integrated-negative-space": {
        "display_name": "柔化分栏式（兼容）",
        "aliases": ["柔化分栏", "soft-split", "integrated-negative-space"],
        "renderer": "edge-blend",
        "default": False,
    },
    "integrated-scene": {
        "display_name": "同场景图文融合式",
        "aliases": ["融合式", "图文融合", "图文一体", "integrated", "integrated-scene"],
        "renderer": "scene-pocket",
        "default": False,
    },
}


def resolve_profile_id(value: str = "") -> str:
    needle = str(value or "safe-split").strip().lower()
    if needle in LAYOUT_PROFILES:
        return needle
    hits = [
        profile_id
        for profile_id, profile in LAYOUT_PROFILES.items()
        if needle in {str(alias).strip().lower() for alias in profile.get("aliases", [])}
    ]
    if len(hits) == 1:
        return hits[0]
    available = "、".join(LAYOUT_PROFILES)
    raise ImageFactoryError(f"未知 layout_profile {value!r}，可选：{available}")


def visual_treatment(profile_id: str, *, width: int, height: int, layout_mode: str) -> dict[str, Any]:
    profile = LAYOUT_PROFILES[resolve_profile_id(profile_id)]
    renderer = str(profile["renderer"])
    if renderer == "rounded-card":
        return {
            "renderer": renderer,
            "corner_radius_ratio": 0.035,
            "shadow": True,
        }
    if renderer == "edge-blend":
        min_dimension = min(width, height)
        return {
            "renderer": renderer,
            "blend_edge": "bottom" if layout_mode == "stacked" else "left",
            "feather_px": max(48, round(min_dimension * 0.075)),
            "corner_radius_px": 0,
            "shadow": False,
        }
    if renderer == "scene-pocket":
        min_dimension = min(width, height)
        return {
            "renderer": renderer,
            "scene_box": "full-canvas",
            "text_island_anchor": "top" if height / width > 1.35 else "left",
            "text_island_shape": "soft-blob",
            "paper_color": "#F7F2E9",
            "paper_opacity": 244,
            "feather_px": max(36, round(min_dimension * 0.045)),
            "corner_radius_px": 0,
            "shadow": False,
        }
    raise ImageFactoryError(f"未知视觉渲染器：{renderer}")


def generation_guidance(profile_id: str, *, layout_mode: str) -> str:
    resolved = resolve_profile_id(profile_id)
    if resolved == "integrated-negative-space":
        direction = "下缘" if layout_mode == "stacked" else "左缘"
        return f"主视觉自然延伸至画布边缘，在靠近文字的{direction}保留干净、低细节的纸张过渡；不要画独立卡片边框或投影。"
    if resolved == "integrated-scene":
        return "主视觉铺满整张画布，在场景内部主动形成一个不规则低细节留白岛；人物、动作线和道具围绕留白岛组织，但不得进入文字字形安全区；不要画左右分栏、独立卡片边框或投影。"
    return "主体完整并集中在视觉区，四周留出安全裁切余量；不要在主体外添加文字或标签。"


def list_profiles() -> list[dict[str, Any]]:
    return [{"id": profile_id, **copy.deepcopy(profile)} for profile_id, profile in LAYOUT_PROFILES.items()]
