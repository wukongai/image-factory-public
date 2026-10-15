"""Canvas presets and adaptive, non-overlapping mixed-layout contracts."""

from __future__ import annotations

import copy
import re
from typing import Any

from image_factory.errors import ImageFactoryError
from image_factory import layout_profiles


CANVAS_PRESETS: dict[str, dict[str, Any]] = {
    "square_1_1": {
        "display_name": "方图 1:1",
        "size": "2048x2048",
        "aliases": ["1:1", "方图", "正方形", "square"],
    },
    "portrait_3_4": {
        "display_name": "竖图 3:4",
        "size": "1536x2048",
        "aliases": ["3:4", "竖图", "小红书竖图", "portrait"],
    },
    "vertical_9_16": {
        "display_name": "竖屏视频 9:16",
        "size": "1152x2048",
        "aliases": ["9:16", "竖屏", "竖屏视频", "short-video"],
    },
    "landscape_16_9": {
        "display_name": "横图 16:9",
        "size": "2048x1152",
        "aliases": ["16:9", "横图", "视频横图", "landscape"],
    },
    "wechat_cover_2_35_1": {
        "display_name": "公众号头图 2.35:1",
        "size": "1880x800",
        "aliases": ["2.35:1", "公众号头图", "公众号封面", "wechat-cover"],
    },
}

SIZE_PATTERN = re.compile(r"^\s*(\d+)\s*[xX×]\s*(\d+)\s*$")


def parse_size(value: str) -> tuple[int, int]:
    match = SIZE_PATTERN.match(str(value or ""))
    if not match:
        raise ImageFactoryError(f"图片尺寸必须为 宽x高，例如 1152x2048：{value!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if width < 320 or height < 320 or width > 8192 or height > 8192:
        raise ImageFactoryError("图片宽高必须介于 320 与 8192 像素之间")
    return width, height


def preset_id(value: str) -> str:
    needle = str(value or "").strip().lower()
    if needle in CANVAS_PRESETS:
        return needle
    hits = [
        key
        for key, preset in CANVAS_PRESETS.items()
        if needle in {str(alias).strip().lower() for alias in preset.get("aliases", [])}
    ]
    if len(hits) == 1:
        return hits[0]
    available = "、".join(CANVAS_PRESETS)
    raise ImageFactoryError(f"未知画布预设 {value!r}，可选：{available}")


def _rect(width: int, height: int, box: tuple[float, float, float, float]) -> dict[str, int]:
    x, y, w, h = box
    return {
        "x": round(width * x),
        "y": round(height * y),
        "w": round(width * w),
        "h": round(height * h),
    }


def _layout_boxes(width: int, height: int, mode: str) -> dict[str, dict[str, int]]:
    if mode == "stacked":
        return {
            "header": _rect(width, height, (0.055, 0.035, 0.89, 0.105)),
            "visual": _rect(width, height, (0.055, 0.17, 0.89, 0.48)),
            "headline": _rect(width, height, (0.055, 0.68, 0.89, 0.12)),
            "support": _rect(width, height, (0.055, 0.82, 0.89, 0.065)),
            "footer": _rect(width, height, (0.055, 0.91, 0.89, 0.04)),
        }
    if mode == "split-left":
        return {
            "header": _rect(width, height, (0.055, 0.055, 0.38, 0.085)),
            "headline": _rect(width, height, (0.055, 0.18, 0.38, 0.30)),
            "support": _rect(width, height, (0.055, 0.54, 0.38, 0.17)),
            "visual": _rect(width, height, (0.49, 0.055, 0.455, 0.70)),
            "footer": _rect(width, height, (0.055, 0.84, 0.89, 0.075)),
        }
    if mode == "banner":
        return {
            "header": _rect(width, height, (0.045, 0.08, 0.39, 0.10)),
            "headline": _rect(width, height, (0.045, 0.23, 0.39, 0.35)),
            "support": _rect(width, height, (0.045, 0.64, 0.39, 0.16)),
            "visual": _rect(width, height, (0.48, 0.06, 0.475, 0.76)),
            "footer": _rect(width, height, (0.045, 0.88, 0.91, 0.055)),
        }
    raise ImageFactoryError(f"不支持的自适应布局：{mode}")


def _scene_pocket_boxes(width: int, height: int) -> dict[str, dict[str, int]]:
    """Text zones inside a shared full-canvas scene.

    The visual zone intentionally covers the whole canvas.  The compositor
    masks image detail beneath the text island before drawing text.
    """

    ratio = width / height
    if ratio < 0.82:
        return {
            "visual": {"x": 0, "y": 0, "w": width, "h": height},
            "header": _rect(width, height, (0.075, 0.055, 0.85, 0.055)),
            "headline": _rect(width, height, (0.075, 0.125, 0.85, 0.18)),
            "support": _rect(width, height, (0.075, 0.32, 0.85, 0.10)),
            "footer": _rect(width, height, (0.075, 0.88, 0.85, 0.045)),
        }
    return {
        "visual": {"x": 0, "y": 0, "w": width, "h": height},
        "header": _rect(width, height, (0.055, 0.075, 0.40, 0.075)),
        "headline": _rect(width, height, (0.055, 0.17, 0.40, 0.25)),
        "support": _rect(width, height, (0.055, 0.45, 0.40, 0.14)),
        "footer": _rect(width, height, (0.055, 0.86, 0.89, 0.055)),
    }


def _overlap(a: dict[str, int], b: dict[str, int]) -> bool:
    return not (
        a["x"] + a["w"] <= b["x"]
        or b["x"] + b["w"] <= a["x"]
        or a["y"] + a["h"] <= b["y"]
        or b["y"] + b["h"] <= a["y"]
    )


def validate_zones(
    zones: dict[str, dict[str, int]],
    width: int,
    height: int,
    *,
    allow_visual_overlap: bool = False,
) -> None:
    for name, box in zones.items():
        if (
            box["x"] < 0
            or box["y"] < 0
            or box["w"] <= 0
            or box["h"] <= 0
            or box["x"] + box["w"] > width
            or box["y"] + box["h"] > height
        ):
            raise ImageFactoryError(f"混排区域超出画布：{name}={box}")
    names = list(zones)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            if allow_visual_overlap and "visual" in {left, right}:
                continue
            if _overlap(zones[left], zones[right]):
                raise ImageFactoryError(f"混排区域不得重叠：{left} 与 {right}")


def build_layout_plan(
    *,
    size: str,
    preset: str = "",
    layout_mode: str = "auto",
    layout_profile: str = "safe-split",
) -> dict[str, Any]:
    width, height = parse_size(size)
    ratio = width / height
    if layout_mode == "auto":
        if ratio < 0.82:
            mode = "stacked"
        elif ratio <= 1.45:
            mode = "split-left"
        else:
            mode = "banner"
    else:
        mode = layout_mode
    resolved_profile = layout_profiles.resolve_profile_id(layout_profile)
    if resolved_profile != "safe-split" and layout_mode not in {"", "auto"}:
        raise ImageFactoryError("显式 layout_profile 与 layout_mode 不能同时指定，请让 profile 自动选择几何布局")
    if resolved_profile == "integrated-scene":
        mode = "scene-pocket"
        zones = _scene_pocket_boxes(width, height)
        validate_zones(zones, width, height, allow_visual_overlap=True)
    else:
        zones = _layout_boxes(width, height, mode)
        validate_zones(zones, width, height)
    min_dimension = min(width, height)
    return {
        "schema_version": "adaptive-mixed-layout/v1",
        "canvas_preset": preset or "custom",
        "size": f"{width}x{height}",
        "width": width,
        "height": height,
        "aspect_ratio": round(ratio, 4),
        "layout_mode": mode,
        "layout_profile": resolved_profile,
        "visual_treatment": layout_profiles.visual_treatment(
            resolved_profile,
            width=width,
            height=height,
            layout_mode=mode,
        ),
        "zones": zones,
        "typography": {
            "headline_min_px": max(34, round(min_dimension * 0.038)),
            "support_min_px": max(24, round(min_dimension * 0.024)),
            "header_min_px": max(22, round(min_dimension * 0.021)),
        },
        "constraints": {
            "non_overlap": resolved_profile != "integrated-scene",
            "visual_only_in_visual_zone": resolved_profile != "integrated-scene",
            "text_only_in_text_zones": True,
            "full_canvas_scene": resolved_profile == "integrated-scene",
            "text_island_background_deterministic": resolved_profile == "integrated-scene",
            "no_card_frame": resolved_profile == "integrated-scene",
            "no_split_column": resolved_profile == "integrated-scene",
            "fail_if_text_does_not_fit": True,
            "raw_image_must_not_generate_text": True,
        },
    }


def resolve_canvas(
    *,
    requested_preset: str = "",
    requested_size: str = "",
    fallback_size: str,
    layout_mode: str = "auto",
    layout_profile: str = "safe-split",
) -> tuple[str, str, dict[str, Any]]:
    if requested_preset and requested_size:
        raise ImageFactoryError("不能同时指定 canvas_preset 和 size，请二选一")
    if requested_preset:
        resolved_id = preset_id(requested_preset)
        size = str(CANVAS_PRESETS[resolved_id]["size"])
    else:
        resolved_id = "custom"
        size = requested_size or fallback_size
    width, height = parse_size(size)
    canonical_size = f"{width}x{height}"
    return resolved_id, canonical_size, build_layout_plan(
        size=canonical_size,
        preset=resolved_id,
        layout_mode=layout_mode,
        layout_profile=layout_profile,
    )


def list_presets() -> list[dict[str, Any]]:
    return [
        {"id": key, **copy.deepcopy(value)}
        for key, value in CANVAS_PRESETS.items()
    ]
