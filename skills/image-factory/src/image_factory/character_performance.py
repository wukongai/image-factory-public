"""Structured IP character-performance presets and validation."""

from __future__ import annotations

from typing import Any

from image_factory.errors import ImageFactoryError


EXPRESSION_PRESETS: dict[str, dict[str, Any]] = {
    "calm-explain": {"label": "平静讲解", "cues": ["目光稳定", "嘴角放松", "眉形中性"]},
    "focused-operate": {"label": "专注操作", "cues": ["视线锁定动作对象", "轻收眉", "闭口专注"]},
    "skeptical-check": {"label": "怀疑审视", "cues": ["单侧挑眉", "轻微歪嘴", "头部微倾"]},
    "realization": {"label": "恍然大悟", "cues": ["眼睛略睁", "眉毛抬起", "身体微前倾"]},
    "concerned-warning": {"label": "担忧提醒", "cues": ["眉心轻收", "嘴角克制", "看向风险源"]},
    "confident-conclusion": {"label": "笃定收束", "cues": ["目光稳定", "轻微闭口笑", "姿态确定"]},
    "playful-deadpan": {"label": "一本正经地搞怪", "cues": ["冷静无辜表情", "与荒谬动作形成反差"]},
}
EXPRESSION_ALIASES = {
    "平静讲解": "calm-explain",
    "专注操作": "focused-operate",
    "怀疑审视": "skeptical-check",
    "恍然大悟": "realization",
    "担忧提醒": "concerned-warning",
    "笃定收束": "confident-conclusion",
    "一本正经地搞怪": "playful-deadpan",
}
INTENSITIES = {"subtle", "balanced", "strong"}
HEAD_POSES = {"neutral", "slight-tilt", "lean-in", "turn-back"}
ALLOWED_FIELDS = {"expression_preset", "intensity", "gaze_target", "facial_cues", "head_pose"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize(value: Any) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise ImageFactoryError("composition.character_performance 必须为 object")
    unknown = sorted(set(value) - ALLOWED_FIELDS)
    if unknown:
        raise ImageFactoryError(f"character_performance 含未知字段：{unknown}")

    raw_preset = _text(value.get("expression_preset")) or "focused-operate"
    preset = EXPRESSION_ALIASES.get(raw_preset, raw_preset)
    if preset not in EXPRESSION_PRESETS:
        available = "、".join(EXPRESSION_PRESETS)
        raise ImageFactoryError(f"未知 expression_preset {raw_preset!r}，可选：{available}")
    intensity = _text(value.get("intensity")) or "subtle"
    if intensity not in INTENSITIES:
        raise ImageFactoryError(f"未知表情强度 {intensity!r}，可选：subtle、balanced、strong")
    gaze_target = _text(value.get("gaze_target")) or "current-action-object"
    if len(gaze_target) > 80:
        raise ImageFactoryError("gaze_target 最多 80 个字符")
    cues = value.get("facial_cues", [])
    if not isinstance(cues, list) or any(not _text(item) for item in cues):
        raise ImageFactoryError("facial_cues 必须为非空字符串数组")
    if len(cues) > 2:
        raise ImageFactoryError("facial_cues 最多 2 个，避免面部提示互相冲突")
    head_pose = _text(value.get("head_pose")) or "neutral"
    if head_pose not in HEAD_POSES:
        raise ImageFactoryError(f"未知 head_pose {head_pose!r}，可选：{', '.join(sorted(HEAD_POSES))}")
    return {
        "expression_preset": preset,
        "intensity": intensity,
        "gaze_target": gaze_target,
        "facial_cues": [_text(item) for item in cues],
        "head_pose": head_pose,
    }


def prompt_lines(value: dict[str, Any]) -> list[str]:
    preset_id = str(value["expression_preset"])
    preset = EXPRESSION_PRESETS[preset_id]
    cues = value.get("facial_cues") or preset["cues"]
    return [
        f"- 表情预设: {preset_id}（{preset['label']}）",
        f"- 表现强度: {value['intensity']}",
        f"- 视线目标: {value['gaze_target']}",
        f"- 面部线索: {'、'.join(cues)}",
        f"- 头部姿态: {value['head_pose']}",
        "- 表情必须保持成熟身份锚点，并服务当前动作与认知隐喻。",
    ]
