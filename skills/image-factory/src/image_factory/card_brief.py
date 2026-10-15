"""card_brief(knowledge-card-brief/v1) → image_brief(image-asset-brief/v1) 映射。

这是知识卡片分支接入通用图像工厂底座的桥:旧 /知识卡片 链路继续消费 card_brief,
通用 CLI 拿到 card_brief 时先经这里转成 image_brief,再走统一的模板编译流程。

设计要点:
- 纯函数,不读文件、不依赖 cli,任何下游都可以直接 import 复用
- 映射做"降维不丢信息":header/modules/footer 摘要进通用 content 字段,
  同时把完整卡片结构原样保留在 image_brief["knowledge_card"] 里,
  prompt 编译时整个 JSON 会进入【稳定内容结构】块,图像模型仍能看到全部模块细节
"""

from __future__ import annotations

from typing import Any

CARD_BRIEF_SCHEMA_PREFIX = "knowledge-card-brief/"
SCENE_KNOWLEDGE_CARD = "knowledge_card"
HEADER_KICKER_MAX_CHARS = 24
HEADER_TITLE_MAX_CHARS = 16
HEADER_SUBTITLE_MAX_CHARS = 24
SUMMARY_MAX_CHARS = 36
MODULE_TITLE_MAX_CHARS = 10
MODULE_COPY_MAX_CHARS = 32
MODULE_KEYWORD_MAX_CHARS = 10
MODULE_VISUAL_MAX_CHARS = 42
SOURCE_REF_MAX_CHARS = 80
FOOTER_SUMMARY_MAX_CHARS = 56
FOOTER_ACTION_MAX_CHARS = 18
MAX_MODULES = 4


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def is_card_brief(data: Any) -> bool:
    """通过 schema_version 前缀判断是否为知识卡片 brief。"""
    return isinstance(data, dict) and _text(data.get("schema_version")).startswith(CARD_BRIEF_SCHEMA_PREFIX)


def _append_text_length_error(errors: list[str], path: str, value: Any, limit: int) -> None:
    text = _text(value)
    if text and len(text) > limit:
        preview = text[:36].rstrip()
        if len(text) > 36:
            preview += "..."
        errors.append(f"{path} 长度 {len(text)}>{limit}: {preview}")


def validate_card_brief_contract(card: dict[str, Any]) -> None:
    """Reject authored card_brief fields that downstream normalize steps would truncate."""
    if not is_card_brief(card):
        raise ValueError("不是 knowledge-card-brief,无法校验")

    errors: list[str] = []
    header = _dict(card.get("header"))
    footer = _dict(card.get("footer"))
    modules = [module for module in card.get("modules") or [] if isinstance(module, dict)]

    _append_text_length_error(errors, "header.kicker", header.get("kicker"), HEADER_KICKER_MAX_CHARS)
    _append_text_length_error(errors, "header.title", header.get("title"), HEADER_TITLE_MAX_CHARS)
    _append_text_length_error(errors, "header.subtitle", header.get("subtitle"), HEADER_SUBTITLE_MAX_CHARS)
    _append_text_length_error(errors, "summary", card.get("summary"), SUMMARY_MAX_CHARS)
    _append_text_length_error(errors, "footer.summary", footer.get("summary"), FOOTER_SUMMARY_MAX_CHARS)
    _append_text_length_error(errors, "footer.action", footer.get("action"), FOOTER_ACTION_MAX_CHARS)

    if not modules:
        errors.append("modules 不能为空")
    if len(modules) > MAX_MODULES:
        errors.append(f"modules 数量 {len(modules)}>{MAX_MODULES};下游会丢弃多余模块")
    for index, module in enumerate(modules, start=1):
        _append_text_length_error(errors, f"modules[{index}].title", module.get("title"), MODULE_TITLE_MAX_CHARS)
        _append_text_length_error(errors, f"modules[{index}].copy", module.get("copy"), MODULE_COPY_MAX_CHARS)
        _append_text_length_error(errors, f"modules[{index}].visual", module.get("visual"), MODULE_VISUAL_MAX_CHARS)
        _append_text_length_error(errors, f"modules[{index}].source_ref", module.get("source_ref"), SOURCE_REF_MAX_CHARS)
        keywords = module.get("keywords") if isinstance(module.get("keywords"), list) else []
        if len(keywords) > 3:
            errors.append(f"modules[{index}].keywords 数量 {len(keywords)}>3;下游会丢弃多余关键词")
        for keyword_index, keyword in enumerate(keywords, start=1):
            _append_text_length_error(
                errors,
                f"modules[{index}].keywords[{keyword_index}]",
                keyword,
                MODULE_KEYWORD_MAX_CHARS,
            )

    if errors:
        raise ValueError(
            "card_brief 含会被 normalize 截断/改写的字段,请先修 brief 再进入 dry-run/raw: "
            + " / ".join(errors)
        )


def card_brief_to_image_brief(card: dict[str, Any]) -> dict[str, Any]:
    """把 card_brief 映射成通用 image_brief。

    字段对应关系:
    - header.title/subtitle → content.headline/subheadline
    - summary → content.summary
    - modules[].title+copy → content.points(最多 4 条,供通用 content 摘要使用)
    - modules[].visual → visual.metaphors
    - footer.action → content.cta
    - header.kicker → content.brand(作为系列/署名标识)
    - 完整卡片结构 → knowledge_card 子对象(原样保留)
    """
    if not is_card_brief(card):
        raise ValueError("不是 knowledge-card-brief,无法映射成 image_brief")
    validate_card_brief_contract(card)

    header = _dict(card.get("header"))
    footer = _dict(card.get("footer"))
    course = _dict(card.get("course"))
    render_hints = _dict(card.get("render_hints"))
    source = _dict(card.get("source"))
    modules = [module for module in card.get("modules") or [] if isinstance(module, dict)]

    title = _text(header.get("title")) or _text(card.get("id")) or "知识卡片"

    points: list[str] = []
    for module in modules:
        module_title = _text(module.get("title"))
        module_copy = _text(module.get("copy"))
        if module_title and module_copy:
            points.append(f"{module_title}: {module_copy}")
        elif module_title or module_copy:
            points.append(module_title or module_copy)

    metaphors = [_text(module.get("visual")) for module in modules if _text(module.get("visual"))]

    course_name = _text(course.get("name"))
    audience = f"{course_name}学员" if course_name else "课程学员"

    module_titles = " / ".join(_text(module.get("title")) for module in modules if _text(module.get("title")))
    subject = f"围绕『{title}』的模块化知识卡片版面"
    if module_titles:
        subject += f",并列模块: {module_titles}"

    image_brief: dict[str, Any] = {
        "schema_version": "image-asset-brief/v1",
        "id": _text(card.get("id")) or title,
        "scene": SCENE_KNOWLEDGE_CARD,
        "goal": "knowledge_review",
        "audience": audience,
        "template_id": _text(card.get("template_id")),
        "content": {
            "headline": title,
            "subheadline": _text(header.get("subtitle")),
            "summary": _text(card.get("summary")) or _text(footer.get("summary")),
            "points": points,
            "cta": _text(footer.get("action")),
            "brand": _text(header.get("kicker")),
        },
        "visual": {
            "subject": subject,
            "metaphors": metaphors,
            "mood": "清晰、克制、可复习的课程知识卡片",
        },
        "composition": {
            "layout_pattern": _text(render_hints.get("pattern")) or "modular_vertical_infographic",
            "text_zones": ["顶部标题区", "中部模块区", "底部结论区"],
            "reading_flow": "自上而下: 标题 → 并列模块 → 结论",
        },
        # 完整卡片结构原样保留,prompt 的 JSON 块仍能呈现 modules 的关键词、文案与视觉描述
        "knowledge_card": {
            "course": course,
            "header": header,
            "modules": modules,
            "footer": footer,
            "render_hints": render_hints,
        },
    }

    post_assets = card.get("post_assets")
    if isinstance(post_assets, dict):
        image_brief["post_assets"] = post_assets

    # 透传参考图分析:支持「照一张已有卡的样式」内嵌进 card_brief.json 一起版本化。
    # compile_prompt 注入【参考图分析】块是模板无关的,只要 brief 带该字段即可生效;
    # 命令行 --reference-analysis 是在映射之后 merge,这里负责的是「内嵌写法」的保留。
    reference_analysis = card.get("reference_analysis")
    if isinstance(reference_analysis, dict):
        image_brief["reference_analysis"] = reference_analysis

    if source:
        image_brief["source"] = {
            "source_content_file": _text(source.get("path")),
            "source_title": _text(source.get("title")),
        }

    return image_brief
