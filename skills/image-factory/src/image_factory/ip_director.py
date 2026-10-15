"""Image-factory owned director for reusable IP shots.

Content callers provide semantic content only.  This module turns that content
into a deterministic, auditable shot direction: composition family, action,
orientation, visual anchor, expression and text presentation.  It deliberately
does not choose a provider or call an image API.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from image_factory import character_performance


IP_SCENES = {"ip_article_illustration", "ip_video_keyframe"}
COMPOSITION_FAMILIES = (
    "editorial-hero",
    "concept-partner",
    "data-evidence",
    "contrast-split",
    "workflow-stage",
    "seated-analysis",
    "close-up-detail",
)
ORIENTATIONS = ("front", "left", "right", "back-three-quarter")
ANCHORS = ("left", "center", "right")
SHOT_TYPES = COMPOSITION_FAMILIES


def _default_identity():
    """Public distribution has no bundled personal identity."""
    return None, []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _content_text(brief: dict[str, Any]) -> str:
    content = brief.get("content") if isinstance(brief.get("content"), dict) else {}
    values = [content.get("headline"), content.get("subheadline"), content.get("summary")]
    points = content.get("points") if isinstance(content.get("points"), list) else []
    values.extend(points)
    return " ".join(_text(value) for value in values if _text(value)).lower()


def _identity_label(brief: dict[str, Any]) -> str:
    visual = brief.get("visual") if isinstance(brief.get("visual"), dict) else {}
    profile = visual.get("ip_profile") if isinstance(visual.get("ip_profile"), dict) else {}
    label = profile.get("display_name") or profile.get("name") or profile.get("identity")
    return _text(label) if not isinstance(label, dict) and _text(label) else "固定 IP"


def _sequence_index(brief: dict[str, Any]) -> int:
    context = brief.get("director_context") if isinstance(brief.get("director_context"), dict) else {}
    explicit = context.get("sequence_index")
    if isinstance(explicit, int) and explicit >= 0:
        return explicit
    match = re.match(r"\s*0*(\d+)", _text(brief.get("id")))
    return int(match.group(1)) - 1 if match else 0


def _pick_expression(text: str, index: int) -> str:
    if any(token in text for token in ("风险", "失败", "腐化", "避免", "问题", "代价", "警告")):
        return ("concerned-warning", "skeptical-check", "focused-operate")[index % 3]
    if any(token in text for token in ("选择", "判断", "比较", "决策", "架构")):
        return ("skeptical-check", "focused-operate", "realization", "playful-deadpan")[index % 4]
    if any(token in text for token in ("发布", "交付", "完成", "通过", "版本")):
        return ("confident-conclusion", "calm-explain", "realization")[index % 3]
    if any(token in text for token in ("发现", "理解", "第一次", "开始")):
        return ("realization", "calm-explain", "focused-operate")[index % 3]
    return ("focused-operate", "calm-explain", "playful-deadpan")[index % 3]


def _pick_action(text: str, index: int) -> str:
    if any(token in text for token in ("安装", "入口", "接入", "连接")):
        return (
            "把核心模块插入分叉入口并确认两条线路",
            "侧身拉开双入口闸门，让同一内核同时通过",
            "蹲下扣紧连接器，再抬头检查两端信号",
        )[index % 3]
    if any(token in text for token in ("选择", "判断", "比较", "方案")):
        return (
            "俯身转动选择盘，把需求拨向合适的结构",
            "双手托住天平，在两个方案之间重新配重",
            "侧坐拆开错误方案，再把合适模块推到前景",
            "回身拉下决策拉杆，让唯一可行路径亮起",
        )[index % 4]
    if any(token in text for token in ("架构", "五种", "模块", "阶段")):
        return (
            "把不同结构模块按用途排序并扣合",
            "沿着台阶逐级递送模块，检查每一段交接",
            "站在结构剖面旁抽出多余层，只保留必要骨架",
            "绕到装置背面接通分流轨道，让任务各走其路",
        )[index % 4]
    if any(token in text for token in ("修改", "维护", "回滚", "腐化", "保护")):
        return (
            "检查变更模块并拉住回滚绳，阻止旧能力被拉坏",
            "跪坐在打开的能力盒旁，替换故障齿轮并复测",
            "反向拖住越界补丁，把它推回隔离轨道",
            "举起放大镜核对新旧接口，再封住危险裂缝",
        )[index % 4]
    if any(token in text for token in ("发布", "交付", "验证", "版本")):
        return (
            "把通过验证的版本胶囊放入交付盒",
            "推着验收印章越过终点线，为成品落章",
            "从测试轨道接住成品，再递给画面外的使用者",
        )[index % 3]
    return ("推动关键物件", "拆开错误连接", "回收散落经验", "标记结果")[index % 4]


def _pick_family(index: int, text: str) -> str:
    if any(token in text for token in ("比较", "对比", "旧", "新", "架构")):
        return ("contrast-split", "workflow-stage", "concept-partner", "close-up-detail")[index % 4]
    if any(token in text for token in ("数据", "比例", "数字", "证据")):
        return ("data-evidence", "editorial-hero", "seated-analysis")[index % 3]
    return COMPOSITION_FAMILIES[index % len(COMPOSITION_FAMILIES)]


def plan(brief: dict[str, Any], template: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return an Image-factory-owned director patch for an IP brief.

    Explicit composition fields supplied by a trusted caller are preserved by
    the normalizer; the returned plan only fills missing visual direction.
    """

    content = brief.get("content") if isinstance(brief.get("content"), dict) else {}
    headline = _text(content.get("headline")) or "当前内容"
    text = _content_text(brief)
    index = _sequence_index(brief)
    family = _pick_family(index, text)
    action = _pick_action(text, index)
    expression = _pick_expression(text, index)
    orientation = ORIENTATIONS[index % len(ORIENTATIONS)]
    anchor = ANCHORS[index % len(ANCHORS)]
    gaze_target = ("当前动作对象", "左侧核心物件", "右侧结果出口", "viewer")[index % 4]
    head_pose = ("neutral", "lean-in", "slight-tilt", "turn-back")[index % 4]
    body_weight = ("planted", "leaning-forward", "seated-shift", "turning-back")[index % 4]
    crop = ("full-body", "waist-up", "medium", "close-up-detail")[index % 4]
    shot_type = family
    if family == "workflow-stage":
        shot_type = "workflow-stage"
    elif family == "close-up-detail":
        shot_type = "close-up-detail"
    elif family == "seated-analysis":
        shot_type = "seated-analysis"

    performance = character_performance.normalize(
        {
            "expression_preset": expression,
            "intensity": "balanced" if expression in {"skeptical-check", "concerned-warning", "realization"} else "subtle",
            "gaze_target": gaze_target,
            "head_pose": head_pose,
        }
    )
    visual_subject = f"围绕“{headline}”，{_identity_label(brief)}亲自{action}，让人物、物件和信息路径组成一个可读的同场景隐喻。"
    composition_plan = {
        "shot_type": shot_type,
        "structure_type": family,
        "composition_family": family,
        "ip_scale": "co-lead" if family not in {"close-up-detail", "data-evidence"} else "analysis-medium",
        "crop": crop,
        "orientation": orientation,
        "body_weight": body_weight,
        "action": action,
        "visual_anchor_position": anchor,
        "text_presentation": "editorial-prominent" if _text(brief.get("scene")) == "ip_article_illustration" else "subtitle-safe",
        "edge_fill_policy": "左右两侧允许接近画布边缘或局部出画；底部必须保留连续、干净的留白空间。",
        "bottom_negative_space": "画布底部最后 12%-15% 保持低细节、无遮挡的纯净地面或背景，不放重要物件、脚、底座、卡片、箭头或动作线。",
        "character_performance": performance,
    }
    if _text(brief.get("scene")) == "ip_article_illustration":
        composition_plan.update(
            {
                "adaptive_layout": True,
                "layout_profile": "integrated-scene",
            }
        )
    return {
        "schema_version": "ip-director-plan/v1",
        "owner": "image-factory",
        "composition": composition_plan,
        "visual": {
            "subject": visual_subject,
            "metaphors": [
                "一个可操作的具体物件承载核心判断",
                "一条主动作路径表达流程或因果关系",
            ],
            "must_show": [
                f"角色必须完成动作：{action}",
                f"角色朝向：{orientation}；视觉主锚点：{anchor}",
            ],
        },
    }


def merge_missing(brief: dict[str, Any], template: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply director defaults without overriding explicit caller choices."""

    patch = plan(brief, template)
    existing_visual = brief.get("visual") if isinstance(brief.get("visual"), dict) else {}
    existing_composition = brief.get("composition") if isinstance(brief.get("composition"), dict) else {}
    director_visual = patch["visual"]
    director_composition = patch["composition"]
    merged_visual = {**director_visual, **existing_visual}
    merged_composition = {**director_composition, **existing_composition}
    if not isinstance(existing_visual.get("ip_profile"), (dict, str)):
        default_profile, default_assets = _default_identity()
        if default_profile is not None:
            merged_visual["ip_profile"] = default_profile
        if not isinstance(existing_visual.get("authorized_assets"), list) and default_assets:
            merged_visual["authorized_assets"] = default_assets
    if not existing_visual.get("metaphors"):
        merged_visual["metaphors"] = director_visual["metaphors"]
    if not existing_visual.get("must_show"):
        merged_visual["must_show"] = director_visual["must_show"]
    if not _text(existing_visual.get("subject")):
        merged_visual["subject"] = director_visual["subject"]
    if not _text(existing_composition.get("action")):
        merged_composition["action"] = director_composition["action"]
    if not isinstance(existing_composition.get("character_performance"), dict):
        merged_composition["character_performance"] = director_composition["character_performance"]
    explicit_composition_keys = sorted(
        key for key in existing_composition if key in director_composition and existing_composition.get(key) not in (None, "", [])
    )
    # The manifest must describe the plan that will actually be compiled.  A
    # generated default is useful as provenance, but returning the pre-merge
    # patch here made director_plan disagree with explicit expert overrides.
    director = {
        "schema_version": patch["schema_version"],
        "owner": patch["owner"],
        "composition": merged_composition,
        "visual": {
            key: merged_visual[key]
            for key in ("subject", "metaphors", "must_show")
            if key in merged_visual
        },
        "provenance": {
            "default_plan": patch["composition"],
            "explicit_composition_overrides": explicit_composition_keys,
        },
    }
    return {"visual": merged_visual, "composition": merged_composition, "director": director}
