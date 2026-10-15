"""Explicit delivery choices for IP article illustrations.

Delivery is orthogonal to template, render style, canvas, and layout. The
compiler never infers it from a channel because the user chooses either a
one-pass integrated illustration or a two-step publish layout.
"""

from __future__ import annotations

from typing import Any

from image_factory.errors import ImageFactoryError


DIRECT_INTEGRATED = "direct-integrated"
TWO_STEP_PUBLISH = "two-step-publish"
CHOICE_REQUIRED_SCENES = {"ip_article_illustration"}

MODES: tuple[dict[str, Any], ...] = (
    {
        "id": DIRECT_INTEGRATED,
        "display_name": "一次生成图文融合",
        "description": "像原始小黑配图一样，一次生成 IP、物件和少量画面文字。",
    },
    {
        "id": TWO_STEP_PUBLISH,
        "display_name": "纯图后二次扩展",
        "description": "先生成纯画面，再扩展画布并合成顶部标题、副标题和说明。",
    },
)
MODE_IDS = {item["id"] for item in MODES}
LEGACY_ALIASES = {
    "text-integrated-final": TWO_STEP_PUBLISH,
}

SELECTION_SOURCES = {"user-explicit", "user-accepted-recommendation"}


def require_confirmed_selection(scene: str, brief: dict[str, Any]) -> dict[str, Any] | None:
    """Stop IP article compilation until the natural-language choice is confirmed."""
    if scene != "ip_article_illustration":
        return None
    receipt = brief.get("selection_receipt")
    if not isinstance(receipt, dict) or receipt.get("status") != "confirmed":
        raise ImageFactoryError(
            "IP 正文配图尚未完成用户选择确认；请先询问‘沿用推荐，还是展开自选’，"
            "确认交付模式、画布和 IP 风格后再生成。"
        )
    source = str(receipt.get("source") or "").strip()
    if source not in SELECTION_SOURCES:
        raise ImageFactoryError(
            "IP 正文配图的 selection_receipt.source 必须是 user-explicit 或 "
            "user-accepted-recommendation；不能由模板或渠道自动推断。"
        )
    missing = [
        key
        for key in ("delivery_mode", "canvas", "style_variant_id")
        if not str(receipt.get(key) or "").strip()
    ]
    if missing:
        raise ImageFactoryError(
            "IP 正文配图的 selection_receipt 缺少已确认字段："
            + "、".join(missing)
            + "；文字策略、画布和渲染风格必须分别确认。"
        )
    brief_mode = str(brief.get("delivery_mode") or "").strip()
    receipt_mode = str(receipt.get("delivery_mode") or "").strip()
    if brief_mode and receipt_mode and brief_mode != receipt_mode:
        raise ImageFactoryError(
            "selection_receipt.delivery_mode 与 brief.delivery_mode 冲突；"
            "文字策略必须只有一个已确认值。"
        )
    return receipt


def list_modes() -> tuple[dict[str, Any], ...]:
    return MODES


def resolve(scene: str, cli_value: Any, brief_value: Any) -> str:
    """Resolve an explicit mode; command-line selection overrides the brief."""

    value = str(cli_value or brief_value or "").strip()
    if not value:
        if scene in CHOICE_REQUIRED_SCENES:
            raise ImageFactoryError(
                "IP 文章配图必须先选择 --delivery-mode："
                "direct-integrated（一次生成图文融合）或 "
                "two-step-publish（纯图后二次扩展）；系统不会默认选择。"
            )
        return ""
    value = LEGACY_ALIASES.get(value, value)
    if value == "illustration-only":
        raise ImageFactoryError(
            "illustration-only 已不再是用户交付模式；请选择 direct-integrated（一次生成）"
            "或 two-step-publish（两次操作）。"
        )
    if value not in MODE_IDS:
        allowed = ", ".join(sorted(MODE_IDS))
        raise ImageFactoryError(f"delivery_mode 不支持 {value!r}；可选：{allowed}")
    if scene not in CHOICE_REQUIRED_SCENES:
        raise ImageFactoryError(
            f"--delivery-mode 当前只适用于 ip_article_illustration，当前 scene={scene!r}"
        )
    return value
