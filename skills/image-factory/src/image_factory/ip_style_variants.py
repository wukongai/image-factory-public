"""Orthogonal render-style selection for IP illustrations.

An IP style changes only the visual surface. It must never select a business
scene, canvas, text strategy, layout, or identity profile.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from image_factory.errors import ImageFactoryError


IP_SCENES = {"ip_article_illustration", "ip_video_keyframe"}

STYLE_VARIANTS: dict[str, dict[str, str]] = {}

CANVAS_RATIOS = {
    "16:9": 16 / 9,
    "1:1": 1.0,
    "3:4": 3 / 4,
    "9:16": 9 / 16,
}


def selected_id(brief: dict[str, Any]) -> str:
    """Return one unambiguous user-selected style id."""

    receipt = brief.get("selection_receipt")
    receipt_id = (
        str(receipt.get("style_variant_id") or "").strip()
        if isinstance(receipt, dict)
        else ""
    )
    visual = brief.get("visual")
    visual_id = (
        str(visual.get("style_variant_id") or "").strip()
        if isinstance(visual, dict)
        else ""
    )
    if receipt_id and visual_id and receipt_id != visual_id:
        raise ImageFactoryError(
            "selection_receipt.style_variant_id 与 visual.style_variant_id 冲突；"
            "风格必须只有一个已确认值。"
        )
    return receipt_id or visual_id


def apply_selection(repo_root: Path, brief: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the selected profile and normalize its id into the brief."""

    scene = str(brief.get("scene") or "").strip()
    if scene not in IP_SCENES:
        return None
    variant_id = selected_id(brief)
    if not variant_id:
        if scene == "ip_article_illustration":
            raise ImageFactoryError(
                "IP 正文配图缺少已确认的 style_variant_id；"
                "请选择简约线稿、毛毡手作、贴画拼贴、松弛手绘、高冲击或艺术版画。"
            )
        return None
    spec = STYLE_VARIANTS.get(variant_id)
    if spec is None:
        available = "、".join(STYLE_VARIANTS)
        raise ImageFactoryError(
            f"未知 IP 风格 {variant_id!r}；可选：{available}"
        )
    profile_path = (
        repo_root
        / "skills"
        / "ip-illustration-factory"
        / "profiles"
        / spec["profile"]
    )
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageFactoryError(f"无法读取 IP 风格 profile：{profile_path}") from exc
    if not isinstance(profile, dict) or profile.get("scope") != "render-style-only":
        raise ImageFactoryError(
            f"IP 风格 profile 必须声明 scope=render-style-only：{profile_path}"
        )

    visual = brief.get("visual")
    if not isinstance(visual, dict):
        visual = {}
        brief["visual"] = visual
    visual["style_variant_id"] = variant_id

    # Identity is owned by visual.ip_profile. A render-style profile may carry
    # private reference notes, but those notes
    # must not overwrite a caller's authorized custom IP identity.
    prompt_profile = copy.deepcopy(profile)
    prompt_profile.pop("inherits_identity_from", None)
    prompt_profile.pop("identity_invariants", None)
    prompt_profile["style_variant_id"] = variant_id
    prompt_profile["display_name"] = spec["display_name"]
    prompt_profile["orthogonal_scope"] = (
        "只覆盖材质、线条、色彩、形状与表面语气；"
        "不得改变业务场景、文字策略、画幅、布局或 visual.ip_profile。"
    )
    return prompt_profile


def list_variants() -> list[dict[str, str]]:
    return [
        {"id": variant_id, **copy.deepcopy(spec)}
        for variant_id, spec in STYLE_VARIANTS.items()
    ]


def validate_template_scene(brief: dict[str, Any], template: dict[str, Any]) -> None:
    """Prevent a render-style choice from swapping the structural business."""

    brief_scene = str(brief.get("scene") or "").strip()
    template_scene = str(template.get("scene") or "").strip()
    if brief_scene in IP_SCENES and template_scene and template_scene != brief_scene:
        raise ImageFactoryError(
            f"IP 业务场景是 {brief_scene}，但所选结构模板属于 {template_scene}；"
            "渲染风格不得通过换模板改变正文/视频业务类型。"
        )


def selected_raw_canvas(brief: dict[str, Any]) -> str:
    receipt = brief.get("selection_receipt")
    if not isinstance(receipt, dict):
        return ""
    return str(receipt.get("canvas") or "").split("->", 1)[0].strip()


def canvas_request_if_template_mismatches(
    brief: dict[str, Any],
    fallback_size: str,
) -> tuple[str, str]:
    """Return a preset/size override only when the template default disagrees."""

    raw_canvas = selected_raw_canvas(brief)
    if not raw_canvas:
        return "", ""
    expected_ratio = CANVAS_RATIOS.get(raw_canvas)
    custom_size = ""
    if expected_ratio is None:
        normalized = raw_canvas.lower().replace("×", "x")
        if "x" in normalized:
            left, right = normalized.split("x", 1)
            if left.isdigit() and right.isdigit() and int(right) > 0:
                expected_ratio = int(left) / int(right)
                custom_size = normalized
    if expected_ratio is None:
        return "", ""

    normalized_fallback = str(fallback_size or "").lower().replace("×", "x")
    try:
        width_text, height_text = normalized_fallback.split("x", 1)
        fallback_ratio = int(width_text) / int(height_text)
    except (ValueError, ZeroDivisionError):
        fallback_ratio = 0.0
    if fallback_ratio and abs(fallback_ratio - expected_ratio) / expected_ratio <= 0.02:
        return "", ""
    if custom_size:
        return "", custom_size
    return raw_canvas, ""


def validate_canvas_selection(brief: dict[str, Any], size: str) -> None:
    """Ensure a structural template cannot silently override the chosen canvas."""

    if str(brief.get("scene") or "").strip() != "ip_article_illustration":
        return
    receipt = brief.get("selection_receipt")
    if not isinstance(receipt, dict):
        return
    canvas_value = str(receipt.get("canvas") or "").strip()
    # A two-step receipt describes raw -> final. This compiler invocation
    # generates the raw image, so only the left-hand canvas is checked here.
    raw_canvas = selected_raw_canvas(brief)
    expected_ratio = CANVAS_RATIOS.get(raw_canvas)
    if expected_ratio is None:
        normalized = raw_canvas.lower().replace("×", "x")
        if "x" in normalized:
            left, right = normalized.split("x", 1)
            if left.isdigit() and right.isdigit() and int(right) > 0:
                expected_ratio = int(left) / int(right)
    if expected_ratio is None:
        available = "、".join(CANVAS_RATIOS)
        raise ImageFactoryError(
            f"无法识别 selection_receipt.canvas={canvas_value!r}；"
            f"请使用 {available}、自定义宽x高，或 raw -> final。"
        )

    normalized_size = str(size or "").lower().replace("×", "x")
    if "x" not in normalized_size:
        raise ImageFactoryError(f"无法校验 IP 画布尺寸：{size!r}")
    width_text, height_text = normalized_size.split("x", 1)
    try:
        actual_ratio = int(width_text) / int(height_text)
    except (ValueError, ZeroDivisionError) as exc:
        raise ImageFactoryError(f"无法校验 IP 画布尺寸：{size!r}") from exc
    if abs(actual_ratio - expected_ratio) / expected_ratio > 0.02:
        raise ImageFactoryError(
            f"用户确认的 raw 画布是 {raw_canvas}，但结构模板将生成 {size}；"
            "画幅不得由模板或渲染风格偷偷改变，请改用匹配的结构基底或显式尺寸。"
        )
