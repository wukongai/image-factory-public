#!/usr/bin/env python3
"""Compile one image asset request from a reusable brief.

This module is deliberately compile-only. It emits a provider-neutral render
handoff and contains no remote execution or routing behavior.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

# 共享模块走包内导入;当本文件被当作独立脚本加载(测试 importlib / 直接执行)时,
# 包可能不在 sys.path 上,此时补上 src 目录再导入一次
try:
    from image_factory import canvas_presets
    from image_factory import character_performance
    from image_factory import delivery_modes
    from image_factory import ip_director
    from image_factory import ip_style_variants
    from image_factory import layout_profiles
    from image_factory import card_brief as card_brief_module
    from image_factory import reference_strategy as reference_strategy_module
    from image_factory import registry
    from image_factory import render_handoff as render_handoff_module
    from image_factory.errors import ImageFactoryError
except ModuleNotFoundError:  # pragma: no cover - 仅脚本式加载时触发
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from image_factory import canvas_presets
    from image_factory import character_performance
    from image_factory import delivery_modes
    from image_factory import ip_director
    from image_factory import ip_style_variants
    from image_factory import layout_profiles
    from image_factory import card_brief as card_brief_module
    from image_factory import reference_strategy as reference_strategy_module
    from image_factory import registry
    from image_factory import render_handoff as render_handoff_module
    from image_factory.errors import ImageFactoryError


DEFAULT_SCENE = "poster"
DEFAULT_SIZE = "1024x1536"
DEFAULT_SOURCE_MAX_CHARS = 8000
PROMPT_REDACT_SOURCE_KEYS = {
    "block_id",
    "blockid",
    "source_block_id",
    "stage_id",
    "stageid",
    "task_id",
    "taskid",
    "camp_id",
    "campid",
    "qr_image_path",
    "qr_asset",
}
_BUSINESS_METADATA_LINE = re.compile(r"^\s*(block_id|blockId|stage_id|stageId|task_id|taskId|camp_id|campId)\s*:")
# 兜底表:正常情况下场景默认模板由模板 JSON 的 scene + default_for_scene 自声明
# (见 registry.scene_default_templates),这里只兜模板文件缺失声明时的底
FALLBACK_TEMPLATE_BY_SCENE = {
    "poster": "poster-reference-remix-v1",
    "marketing_poster": "marketing-poster-conversion-v1",
    "article_illustration": "wechat-article-keypoints-v2",
    "cover": "cover-clean-title-v1",
    "wechat_cover": "wechat-article-cover-v1",
    "video_cover": "video-cover-bold-title-v1",
    "knowledge_card": "warm-paper-handdrawn-quote-entry-v1",
}


def default_template_for_scene(scene: str | None) -> str:
    """场景 → 默认模板 id。优先用模板 JSON 自声明,再查兜底表,最后退回海报模板。"""
    scene_value = scene or DEFAULT_SCENE
    declared = registry.scene_default_templates(repo_root_from_script())
    return (
        declared.get(scene_value)
        or FALLBACK_TEMPLATE_BY_SCENE.get(scene_value)
        or FALLBACK_TEMPLATE_BY_SCENE[DEFAULT_SCENE]
    )


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def slugify(value: str, fallback: str = "image-asset") -> str:
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^0-9a-zA-Z._\-\u4e00-\u9fff]+", "-", value)
    value = value.strip("-._")
    return value or fallback


def clip_text(value: Any, limit: int, suffix: str = "") -> str:
    text = as_text(value)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + suffix


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImageFactoryError(f"{label} 不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ImageFactoryError(f"{label} 不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ImageFactoryError(f"{label} 顶层必须是 object: {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_template(template: str | None, scene: str | None = None) -> Path:
    # 模板发现统一交给 registry: 根目录 templates/ 与 skills/*/templates/ 共享一套查找逻辑
    template_value = template or default_template_for_scene(scene)
    return registry.resolve_template_path(repo_root_from_script(), template_value)


def strip_frontmatter(content: str) -> str:
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        if end != -1:
            return content[end + 4 :].lstrip()
    return content


def first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def read_frontmatter_value(content: str, key: str) -> str:
    if not content.startswith("---\n"):
        return ""
    end = content.find("\n---", 4)
    if end == -1:
        return ""
    frontmatter = content[4:end]
    return first_match(rf"^{re.escape(key)}:\s*(.+?)\s*$", frontmatter)


def clean_source_content(content: str) -> str:
    cleaned_lines: list[str] = []
    in_fence = False
    in_frontmatter = content.startswith("---\n")
    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if in_frontmatter:
            if stripped == "---" and cleaned_lines:
                in_frontmatter = False
                cleaned_lines.append(line)
                continue
            key = stripped.split(":", 1)[0].strip().lower() if ":" in stripped else ""
            if key in PROMPT_REDACT_SOURCE_KEYS:
                continue
        if stripped.startswith("```"):
            in_fence = not in_fence
            cleaned_lines.append(line)
            continue
        if not in_fence and (
            stripped.startswith("<image ")
            or stripped.startswith("<mention-doc ")
            or stripped.startswith("</mention-doc")
            or stripped.startswith("<file ")
            or _BUSINESS_METADATA_LINE.match(stripped)
        ):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned.strip()


def read_content_file(path_value: str, max_chars: int) -> tuple[str, dict[str, Any]]:
    path = Path(path_value).expanduser()
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ImageFactoryError(f"内容文件不存在: {path}") from exc
    if not content:
        raise ImageFactoryError(f"内容文件为空: {path}")
    cleaned = clean_source_content(content)
    truncated = False
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip()
        truncated = True
    metadata = {
        "source_content_file": str(path),
        "source_original_chars": len(content),
        "source_prompt_chars": len(cleaned),
        "source_truncated": truncated,
    }
    return cleaned, metadata


# 英文标识符 + 冒号开头的列表项(如 "content_contract：..."、"layout / style：...")
# 是代码字段说明,放进配图当要点是噪音,提取时跳过
_CODE_FIELD_ITEM = re.compile(r"^[A-Za-z_][A-Za-z0-9_ /]*[:：]")
# 章节标题常见的序号前缀(一、/ 1. / 1、/ （一）),对配图要点无意义,清洗掉
_HEADING_ORDINAL = re.compile(r"^[（(]?[0-9一二三四五六七八九十]+[）)]?\s*[、.．]\s*")


def strip_heading_ordinal(value: Any) -> str:
    """Remove common Markdown section ordinals from display titles."""
    return _HEADING_ORDINAL.sub("", as_text(value)).strip()


def extract_points(source_content: str, limit: int = 4) -> list[str]:
    text = strip_frontmatter(source_content)
    points: list[str] = []

    for raw in re.findall(r"^\s*[-*]\s+(.+)$", text, flags=re.MULTILINE):
        item = re.sub(r"\*\*(.+?)\*\*", r"\1", raw).strip()
        if not item or _CODE_FIELD_ITEM.match(item):
            continue
        if item not in points:
            points.append(clip_text(item, 24, "…"))
        if len(points) >= limit:
            return points

    for raw in re.findall(r"^#{2,4}\s+(.+)$", text, flags=re.MULTILINE):
        item = strip_heading_ordinal(raw)
        if item and item not in points:
            points.append(clip_text(item, 24, "…"))
        if len(points) >= limit:
            return points

    bold_sections = re.findall(r"\*\*(.+?)\*\*\s*[:：](.+)", text)
    for title, copy in bold_sections:
        item = f"{title.strip()}: {copy.strip()}"
        if item and item not in points:
            points.append(clip_text(item, 24, "…"))
        if len(points) >= limit:
            return points

    summary = re.sub(r"\s+", " ", text).strip()
    if summary:
        points.append(clip_text(summary, 28, "…"))
    return points[:limit]


def load_reference_analysis(path_value: str | None, reference_image: str | None = None) -> dict[str, Any]:
    analysis: dict[str, Any] = {}
    if path_value:
        path = Path(path_value).expanduser()
        if path.suffix.lower() == ".json":
            analysis = load_json(path, "参考图分析")
        else:
            try:
                notes = path.read_text(encoding="utf-8").strip()
            except FileNotFoundError as exc:
                raise ImageFactoryError(f"参考图分析文件不存在: {path}") from exc
            analysis = {"notes": notes}
        analysis.setdefault("analysis_file", str(path))
    if reference_image:
        analysis.setdefault("source_image", reference_image)
    return analysis


def parse_override_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def set_nested_value(target: dict[str, Any], path: str, value: Any) -> None:
    if not path.strip():
        raise ImageFactoryError("--set 的 key 不能为空")
    parts = path.split(".")
    current: Any = target
    for part in parts[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise ImageFactoryError(f"--set 路径无效: {path}") from exc
            continue
        if not isinstance(current, dict):
            raise ImageFactoryError(f"--set 路径不能继续深入: {path}")
        if part not in current or current[part] is None:
            current[part] = {}
        current = current[part]

    leaf = parts[-1]
    if isinstance(current, list):
        try:
            current[int(leaf)] = value
        except (ValueError, IndexError) as exc:
            raise ImageFactoryError(f"--set 列表索引无效: {path}") from exc
    elif isinstance(current, dict):
        current[leaf] = value
    else:
        raise ImageFactoryError(f"--set 路径不能写入: {path}")


def apply_template_overrides(template: dict[str, Any], overrides: list[str] | None) -> None:
    for assignment in overrides or []:
        if "=" not in assignment:
            raise ImageFactoryError(f"--set 需要 key=value 格式: {assignment}")
        key, raw_value = assignment.split("=", 1)
        set_nested_value(template, key.strip(), parse_override_value(raw_value.strip()))


def listify(value: Any) -> list[str]:
    if isinstance(value, list):
        return [as_text(item) for item in value if as_text(item)]
    text = as_text(value)
    return [text] if text else []


AUTHORIZED_ASSET_PURPOSES = {"identity", "style", "composition", "content"}


def _normalize_authorized_asset(raw: Any, base_dir: Path) -> dict[str, Any]:
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{"):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ImageFactoryError(f"--authorized-asset 不是合法 JSON: {exc}") from exc
        elif "=" in text:
            role, path = text.split("=", 1)
            raw = {"purpose": role, "path": path, "ownership": "authorized"}
        else:
            raw = {"purpose": "content", "path": text, "ownership": "authorized"}
    if not isinstance(raw, dict):
        raise ImageFactoryError("authorized_visual_assets 每项必须是 object 或 purpose=path")
    path_text = as_text(raw.get("path"))
    purpose = as_text(raw.get("purpose")) or "content"
    if not path_text:
        raise ImageFactoryError("authorized_visual_assets 每项必须提供 path")
    if purpose not in AUTHORIZED_ASSET_PURPOSES:
        raise ImageFactoryError(f"authorized_visual_assets.purpose 不支持: {purpose}")
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    else:
        path = path.resolve()
    if not path.is_file():
        raise ImageFactoryError(f"授权素材不存在: {path}")
    normalized = {
        "path": str(path),
        "purpose": purpose,
        "ownership": as_text(raw.get("ownership")) or "authorized",
    }
    if as_text(raw.get("id")):
        normalized["id"] = as_text(raw.get("id"))
    if "required" in raw:
        normalized["required"] = bool(raw.get("required"))
    return normalized


def normalize_authorized_visual_assets(
    brief: dict[str, Any], args: argparse.Namespace
) -> list[dict[str, Any]]:
    base_dir = (
        Path(args.brief).expanduser().resolve().parent
        if as_text(getattr(args, "brief", ""))
        else Path.cwd()
    )
    visual = brief.get("visual") if isinstance(brief.get("visual"), dict) else {}
    raw_assets = visual.get("authorized_assets")
    assets = list(raw_assets) if isinstance(raw_assets, list) else []
    assets.extend(getattr(args, "authorized_asset", None) or [])
    return [_normalize_authorized_asset(item, base_dir) for item in assets]


def build_brief(args: argparse.Namespace, template: dict[str, Any]) -> dict[str, Any]:
    if args.brief:
        brief = load_json(Path(args.brief).expanduser(), "图像 brief")
        # 知识卡片 brief 自动识别并映射成通用 image_brief,让卡片复用通用底座
        if card_brief_module.is_card_brief(brief):
            try:
                brief = card_brief_module.card_brief_to_image_brief(brief)
            except ValueError as exc:
                raise ImageFactoryError(str(exc)) from exc
    else:
        source_content = ""
        source_metadata: dict[str, Any] = {}
        if args.content_file:
            source_content, source_metadata = read_content_file(args.content_file, args.source_max_chars)

        source_title = read_frontmatter_value(source_content, "title")
        # Obsidian frontmatter 常已写好 hook(钩子句)和 audience(受众),正好对应配图的
        # 副标题与受众定位;命令行参数优先,未传时从 frontmatter 自动补,避免富信息被浪费
        source_hook = read_frontmatter_value(source_content, "hook")
        source_audience = read_frontmatter_value(source_content, "audience")
        headline = args.title or source_title or (Path(args.content_file).stem if args.content_file else "图像素材")
        points = args.point or extract_points(source_content)
        scene = args.scene or as_text(template.get("scene")) or DEFAULT_SCENE

        brief = {
            "schema_version": "image-asset-brief/v1",
            "id": slugify(args.id or headline),
            "scene": scene,
            "goal": args.goal or default_goal_for_scene(scene),
            "audience": args.audience or source_audience or "目标读者",
            "source": source_metadata,
            "content": {
                "headline": clip_text(headline, 28),
                "subheadline": clip_text(args.subtitle or source_hook or args.summary or "", 42),
                "summary": clip_text(args.summary or first_match(r"^>\s*(.+)$", source_content), 60),
                "points": [clip_text(point, 28) for point in points[:4]],
                "cta": clip_text(args.cta, 24),
                "brand": clip_text(args.brand, 24),
            },
            "visual": {
                "subject": args.visual_subject or default_subject_for_scene(scene, headline),
                "metaphors": [],
                "mood": args.mood or as_text(template.get("default_mood")) or "清晰、专业、有记忆点",
            },
            "composition": {
                "format": as_text(template.get("aspect")) or "按模板画幅",
                "layout_pattern": as_text(template.get("default_layout_pattern")) or as_text(template.get("id")),
            },
            "source_content": source_content,
        }

    reference_analysis = load_reference_analysis(args.reference_analysis, args.reference_image)
    if reference_analysis:
        existing = brief.get("reference_analysis") if isinstance(brief.get("reference_analysis"), dict) else {}
        merged = {**existing, **reference_analysis}
        brief["reference_analysis"] = merged

    normalized = normalize_brief(brief, template, args)
    visual = normalized.get("visual") if isinstance(normalized.get("visual"), dict) else {}
    visual["authorized_assets"] = normalize_authorized_visual_assets(normalized, args)
    normalized["visual"] = visual
    leaked_api_fields = [
        field
        for field in ("generation_mode", "reference_inputs", "reference_input_image")
        if field in normalized
    ]
    if leaked_api_fields:
        raise ImageFactoryError(
            "image_brief 只能描述业务素材；执行控制字段不得进入业务 brief: "
            + ", ".join(leaked_api_fields)
        )
    return normalized


def default_goal_for_scene(scene: str) -> str:
    return {
        "poster": "lead_generation",
        "marketing_poster": "lead_generation",
        "article_illustration": "explain_concept",
        "cover": "increase_click_intent",
        "wechat_cover": "increase_open_rate",
        "video_cover": "increase_click_intent",
    }.get(scene, "single_image_asset")


def default_subject_for_scene(scene: str, headline: str) -> str:
    if scene == "article_illustration":
        return f"围绕“{headline}”构建一个清晰的概念隐喻场景"
    if scene in {"cover", "wechat_cover"}:
        return f"一个能支撑“{headline}”大标题的强主体视觉"
    if scene == "video_cover":
        return f"一个支撑“{headline}”点击理由的强对比视频封面主体"
    if scene == "marketing_poster":
        return f"把“{headline}”组织成标题、主视觉、利益点和 CTA 的竖版海报"
    return f"把“{headline}”拆成清晰图层、主视觉和行动入口"


def normalize_brief(brief: dict[str, Any], template: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    scene = as_text(args.scene) or as_text(brief.get("scene")) or as_text(template.get("scene")) or DEFAULT_SCENE
    content = brief.get("content") if isinstance(brief.get("content"), dict) else {}
    visual = brief.get("visual") if isinstance(brief.get("visual"), dict) else {}
    composition = brief.get("composition") if isinstance(brief.get("composition"), dict) else {}

    # Content callers provide semantic points only.  For IP scenes the visual
    # director lives in Image Factory and fills the shot/action/performance
    # contract before prompt compilation.  Explicit expert overrides remain
    # authoritative, but callers no longer need to invent visual directions.
    if scene in ip_director.IP_SCENES:
        directed = ip_director.merge_missing(
            {**brief, "scene": scene, "content": content, "visual": visual, "composition": composition},
            template,
        )
        visual = directed["visual"]
        composition = directed["composition"]
        brief["director"] = directed["director"]

    raw_headline = as_text(content.get("headline")) or as_text(brief.get("title")) or as_text(args.title) or "图像素材"
    headline = strip_heading_ordinal(raw_headline) if scene == "article_illustration" else raw_headline
    headline = headline or raw_headline
    brief["schema_version"] = as_text(brief.get("schema_version")) or "image-asset-brief/v1"
    brief["id"] = slugify(as_text(brief.get("id")) or as_text(args.id) or headline)
    brief["scene"] = scene
    brief["goal"] = as_text(args.goal) or as_text(brief.get("goal")) or default_goal_for_scene(scene)
    brief["audience"] = as_text(args.audience) or as_text(brief.get("audience")) or "目标读者"
    brief["content"] = {
        "headline": clip_text(headline, 32),
        "subheadline": clip_text(content.get("subheadline") or args.subtitle, 48),
        "summary": clip_text(content.get("summary") or args.summary, 72),
        "points": [clip_text(item, 32) for item in listify(content.get("points") or args.point)[:4]],
        "cta": clip_text(content.get("cta") or args.cta, 24),
        "brand": clip_text(content.get("brand") or args.brand, 24),
    }
    # 模板可在 content_contract.exclude_content_fields 显式黑名单要求不渲染某些 content 字段。
    # keypoints(公众号大字板书)就用这个把 subheadline 排除掉,避免在公众号正文配图里
    # 重复文章标题/hook、版面挤局促。模板 JSON 一改即生效,不污染其他模板。
    contract = template.get("content_contract") if isinstance(template.get("content_contract"), dict) else {}
    excluded = listify(contract.get("exclude_content_fields"))
    for field in excluded:
        key = as_text(field)
        if key and key in brief["content"]:
            brief["content"][key] = ""
    # IP scene descriptions often contain the actual physical action, gaze and
    # object relationship.  Keep that expert direction intact; generic
    # article subjects still use the shorter editorial limit below.
    subject_limit = 640 if scene in ip_director.IP_SCENES else 72
    brief["visual"] = {
        "subject": clip_text(visual.get("subject") or args.visual_subject or default_subject_for_scene(scene, headline), subject_limit),
        "metaphors": [clip_text(item, 24) for item in listify(visual.get("metaphors"))[:5]],
        "mood": clip_text(visual.get("mood") or args.mood or as_text(template.get("default_mood")) or "清晰、专业、有记忆点", 42),
        "must_show": listify(visual.get("must_show"))[:6],
        "avoid": listify(visual.get("avoid"))[:8],
        "authorized_assets": list(visual.get("authorized_assets"))
        if isinstance(visual.get("authorized_assets"), list)
        else [],
    }
    # 固定 IP 的角色圣经是跨画面一致性的结构化输入。保留它给 prompt 编译，
    # 同时不把本地引用文件路径当成业务模板的执行参数。
    ip_profile = visual.get("ip_profile")
    if isinstance(ip_profile, dict):
        brief["visual"]["ip_profile"] = ip_profile
    elif as_text(ip_profile):
        brief["visual"]["ip_profile"] = clip_text(ip_profile, 480)
    reference_strategy = visual.get("reference_strategy")
    if isinstance(reference_strategy, (str, dict)):
        brief["visual"]["reference_strategy"] = reference_strategy
    brief["composition"] = {
        "format": as_text(composition.get("format")) or as_text(template.get("aspect")) or "按模板画幅",
        "layout_pattern": as_text(composition.get("layout_pattern")) or as_text(template.get("default_layout_pattern")) or as_text(template.get("id")),
        "text_zones": listify(composition.get("text_zones"))[:6],
        "reading_flow": as_text(composition.get("reading_flow")),
    }
    for key in (
        "canvas_preset",
        "size",
        "layout_mode",
        "layout_profile",
        "publish_preset",
        "publish_extension",
    ):
        if as_text(composition.get(key)):
            brief["composition"][key] = as_text(composition.get(key))
    if composition.get("adaptive_layout") is True:
        brief["composition"]["adaptive_layout"] = True
    if scene in ip_director.IP_SCENES:
        # IP 镜头的动作、裁切、方向、表演和文字留白是跨批次一致性的
        # 执行字段；文章 IP 与视频 IP 共用这一套导演合同。
        for key in (
            "internal_ratio",
            "shot_type",
            "structure_type",
            "composition_family",
            "ip_scale",
            "crop",
            "orientation",
            "body_weight",
            "action",
            "visual_anchor_position",
            "subtitle_safe_zone",
            "text_layout_variant",
            "text_presentation",
            "edge_fill_policy",
            "bottom_negative_space",
        ):
            value = composition.get(key)
            if isinstance(value, (dict, list)):
                brief["composition"][key] = value
            elif as_text(value):
                brief["composition"][key] = as_text(value)
        performance = character_performance.normalize(composition.get("character_performance"))
        if performance is not None:
            brief["composition"]["character_performance"] = performance
    return brief


def render_config_value(value: Any, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            label = str(key)
            if isinstance(child, (dict, list)):
                lines.append(f"{prefix}- {label}:")
                lines.extend(render_config_value(child, indent + 1))
            elif as_text(child):
                lines.append(f"{prefix}- {label}: {as_text(child)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(render_config_value(item, indent + 1))
            elif as_text(item):
                lines.append(f"{prefix}- {as_text(item)}")
    elif as_text(value):
        lines.append(f"{prefix}- {as_text(value)}")
    return lines


def append_config_block(lines: list[str], title: str, value: Any) -> None:
    block = render_config_value(value)
    if block:
        lines.extend(["", f"【{title}】"])
        lines.extend(block)


def append_post_component_safe_zones(lines: list[str], components: Any) -> None:
    if not isinstance(components, list):
        return
    safe_lines: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        if as_text(component.get("prompt_exposure")) != "safe_zone_only":
            continue
        safe_zone = component.get("prompt_safe_zone")
        if isinstance(safe_zone, dict):
            guidance = as_text(safe_zone.get("guidance"))
            forbidden = listify(safe_zone.get("forbidden"))
        else:
            guidance = as_text(component.get("prompt_guidance"))
            forbidden = []
        if guidance:
            safe_lines.append(f"- {guidance}")
        safe_lines.extend(f"- 不要在该区域放置: {item}" for item in forbidden)
    if safe_lines:
        lines.extend(["", "【后期安全区】"])
        lines.extend(safe_lines)


PROMPT_FORBIDDEN_TERMS_WHEN_POSTPROCESSING = ["二维码", "扫码", "QR", "qr", "白块", "占" + "位符", "进营"]
PROMPT_REDACT_KEYS = {
    "block_id",
    "blockId",
    "source_block_id",
    "stage_id",
    "stageId",
    "task_id",
    "taskId",
    "camp_id",
    "campId",
    "qr_slot",
    "qr_asset",
    "qr_image_path",
    "post_assets",
    "post_components",
    "template_id",
    "image_factory_meta",
}


def parse_canvas_size(size: str) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", size)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def box_to_xyxy(box: Any, label: str) -> tuple[int, int, int, int]:
    if not isinstance(box, dict):
        raise ImageFactoryError(f"post_components.{label} 必须是 object")
    try:
        x = int(box["x"])
        y = int(box["y"])
        w = int(box["w"])
        h = int(box["h"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ImageFactoryError(f"post_components.{label} 必须包含整数 x/y/w/h") from exc
    if w <= 0 or h <= 0:
        raise ImageFactoryError(f"post_components.{label} 的 w/h 必须大于 0")
    return x, y, x + w, y + h


def assert_box_in_canvas(box: tuple[int, int, int, int], canvas: tuple[int, int], label: str) -> None:
    width, height = canvas
    x0, y0, x1, y1 = box
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
        raise ImageFactoryError(f"post_components.{label} 超出画布 {width}x{height}: {box}")


def validate_post_components(template: dict[str, Any], size: str) -> bool:
    """Validate deterministic post components.

    Returns True when the template asks the prompt to expose only a safe zone.
    In that mode compile_prompt also runs a forbidden-term gate so the model is
    not instructed to draw the deterministic component itself.
    """
    components = template.get("post_components")
    if not isinstance(components, list) or not components:
        return False
    canvas = parse_canvas_size(size)
    if not canvas:
        raise ImageFactoryError(f"post_components 需要可解析的 size,例如 1024x1536: {size}")

    safe_zone_only = False
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise ImageFactoryError(f"post_components[{index}] 必须是 object")
        component_id = as_text(component.get("id")) or f"post_components[{index}]"
        if as_text(component.get("prompt_exposure")) == "safe_zone_only":
            safe_zone_only = True
        boxes = {
            "component_box_px": box_to_xyxy(component.get("component_box_px"), "component_box_px"),
            "replace_box_px": box_to_xyxy(component.get("replace_box_px"), "replace_box_px"),
            "label_box_px": box_to_xyxy(component.get("label_box_px"), "label_box_px"),
            "clean_box_px": box_to_xyxy(component.get("clean_box_px"), "clean_box_px"),
        }
        for label, box in boxes.items():
            assert_box_in_canvas(box, canvas, f"{component_id}.{label}")
    return safe_zone_only


def validate_postprocess_prompt(prompt: str) -> None:
    hits = [term for term in PROMPT_FORBIDDEN_TERMS_WHEN_POSTPROCESSING if term in prompt]
    if hits:
        raise ImageFactoryError(
            "启用 deterministic post_components 时,prompt 不允许出现会诱导模型绘制后期组件的词: "
            + " / ".join(hits)
        )


def append_list_block(lines: list[str], title: str, values: list[str]) -> None:
    clean_values = [value for value in values if value]
    if clean_values:
        lines.extend(["", f"【{title}】"])
        lines.extend(f"- {value}" for value in clean_values)


def parse_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = as_text(value).lower()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    return None


def should_embed_source_content(template: dict[str, Any], brief: dict[str, Any]) -> bool:
    """Return whether raw source text should be included in the compiled prompt."""
    contract = template.get("content_contract") if isinstance(template.get("content_contract"), dict) else {}
    for value in (
        contract.get("embed_source_content_in_prompt"),
        template.get("embed_source_content_in_prompt"),
    ):
        parsed = parse_optional_bool(value)
        if parsed is not None:
            return parsed

    scene = as_text(brief.get("scene")) or as_text(template.get("scene"))
    if scene == "article_illustration":
        return False
    return True


def redact_prompt_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: redact_prompt_payload(child)
            for key, child in value.items()
            if key not in PROMPT_REDACT_KEYS
        }
    if isinstance(value, list):
        return [redact_prompt_payload(item) for item in value]
    return value


def brief_for_prompt(brief: dict[str, Any], template: dict[str, Any] | None = None) -> dict[str, Any]:
    prompt_brief = redact_prompt_payload(json.loads(json.dumps(brief, ensure_ascii=False)))
    contract = template.get("content_contract") if isinstance(template, dict) and isinstance(template.get("content_contract"), dict) else {}
    if as_text(contract.get("prompt_content_mode")) == "visual-only":
        prompt_brief.pop("content", None)
    source = prompt_brief.get("source")
    if isinstance(source, dict):
        source.pop("source_content_file", None)
        source.pop("source_block_id", None)
    prompt_brief.pop("source_content", None)
    prompt_brief.pop("template_id", None)
    prompt_brief.pop("post_assets", None)
    prompt_brief.pop("post_components", None)
    composition = prompt_brief.get("composition")
    if isinstance(composition, dict):
        composition.pop("layout_contract", None)
    return prompt_brief


def compile_prompt(
    template: dict[str, Any],
    brief: dict[str, Any],
    size: str,
    render_style_profile: dict[str, Any] | None = None,
) -> str:
    safe_zone_only_postprocess = validate_post_components(template, size)
    content = brief.get("content") if isinstance(brief.get("content"), dict) else {}
    visual = brief.get("visual") if isinstance(brief.get("visual"), dict) else {}
    composition = brief.get("composition") if isinstance(brief.get("composition"), dict) else {}
    reference_analysis = brief.get("reference_analysis") if isinstance(brief.get("reference_analysis"), dict) else {}
    style = template.get("style") if isinstance(template.get("style"), dict) else {}
    contract = template.get("content_contract") if isinstance(template.get("content_contract"), dict) else {}
    visual_only = as_text(contract.get("prompt_content_mode")) == "visual-only"
    delivery_mode = as_text(brief.get("delivery_mode"))
    direct_integrated = delivery_mode == delivery_modes.DIRECT_INTEGRATED
    two_step_publish = delivery_mode == delivery_modes.TWO_STEP_PUBLISH
    adaptive_layout = (
        composition.get("layout_contract")
        if isinstance(composition.get("layout_contract"), dict)
        else None
    )

    headline = as_text(content.get("headline"))
    if not headline:
        raise ImageFactoryError("brief.content.headline 不能为空")

    lines: list[str] = [
        as_text(template.get("prompt_role")) or "你是资深中文视觉设计师和 AI 生图提示词编排师。",
        (
            "请生成一张用于二次扩展排版的无字原始视觉素材。只生成主体画面，不要生成任何文字、标题、字幕或标签。"
            if adaptive_layout or two_step_publish
            else "请一次生成一张图文融合的 IP 正文配图。文字必须是画面中的少量短标题、标签或手写说明，与人物、物件和动作共同表达一个判断；不要生成长段落、文字墙或乱码。"
            if direct_integrated
            else "请生成一张可直接用于内容发布的中文图片素材。先遵守 image_brief,再执行模板规则;不要自由添加无关品牌、署名或新文案。"
        ),
        "",
        "【图片规格】",
        f"- 尺寸: {size}",
        f"- 模板: {as_text(template.get('name')) or as_text(template.get('id'))}",
        f"- 场景: {as_text(brief.get('scene'))}",
        f"- 目标: {as_text(brief.get('goal'))}",
        f"- 受众: {as_text(brief.get('audience'))}",
    ]
    if not visual_only:
        lines.extend(["", "【用户自己的内容】", f"- 主标题: {headline}"])
        optional_fields = [
            ("subheadline", "副标题"),
            ("summary", "摘要"),
            ("cta", "行动引导"),
            ("brand", "品牌/署名"),
        ]
        for key, label in optional_fields:
            value = as_text(content.get(key))
            if value:
                lines.append(f"- {label}: {value}")
        append_list_block(lines, "要点", listify(content.get("points")))

    lines.extend(["", "【主视觉意图】"])
    for key, label in [("subject", "主体"), ("mood", "情绪")]:
        value = as_text(visual.get(key))
        if value:
            lines.append(f"- {label}: {value}")
    append_list_block(lines, "视觉隐喻", listify(visual.get("metaphors")))
    append_list_block(lines, "必须出现", listify(visual.get("must_show")))
    append_list_block(lines, "必须避免", listify(visual.get("avoid")))

    composition_for_prompt = dict(composition)
    performance = composition_for_prompt.pop("character_performance", None)
    composition_for_prompt.pop("layout_contract", None)
    composition_lines = render_config_value(composition_for_prompt)
    if composition_lines:
        lines.extend(["", "【构图意图】"])
        lines.extend(composition_lines)

    if as_text(brief.get("scene")) in ip_director.IP_SCENES:
        lines.extend(
            [
                "",
                "【底部留白硬约束】",
                "- 左右两侧可以满，可以让人物、装置或动作线接近边缘；不要为了留白把画面缩成居中卡片。",
                "- 画布底部最后 12%-15% 必须保留连续、干净的低细节空间，作为后续标题带、字幕和安全边距的缓冲。",
                "- 重要物件、人物脚部、底座、卡片、箭头、绳索和动作线不得贴到或切出画布底边。",
                "- 底部留白应与背景自然融合，不要画白色矩形占位框、字幕框或装饰横条。",
            ]
        )

    if isinstance(performance, dict):
        lines.extend(["", "【角色表演】"])
        lines.extend(character_performance.prompt_lines(performance))

    if reference_analysis:
        lines.extend(
            [
                "",
                "【参考图分析】",
                "参考图只用于迁移构图、层级、节奏和风格原则。严禁复刻原图品牌、logo、人物肖像、完整文案、受保护 IP 或可识别商业包装。",
            ]
        )
        lines.extend(render_config_value(reference_analysis))

    source_content = as_text(brief.get("source_content"))
    if source_content:
        if should_embed_source_content(template, brief):
            lines.extend(
                [
                    "",
                    "【待解析原文】",
                    "下面原文只用于理解事实和提炼隐喻。画面文字以 image_brief.content 为准,不要把原文整段搬进图片。",
                    '"""',
                    source_content,
                    '"""',
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "【原文处理策略】",
                    "- 原文已在 brief 生成阶段提炼为 headline/summary/points/visual/composition;编译 prompt 不再嵌入完整原文。",
                    "- 画面只执行上方结构化内容,不要尝试复原或搬运整篇文章。",
                ]
            )

    lines.extend(
        [
            "",
            "【稳定内容结构 image_brief】",
            "下面 JSON 是本图的唯一内容结构来源。请严格按照 content/visual/composition/reference_analysis 渲染,不要新增无关模块。",
            "```json",
            json.dumps(brief_for_prompt(brief, template), ensure_ascii=False, indent=2),
            "```",
        ]
    )

    append_config_block(lines, "模板参数", template.get("template_parameters"))
    append_config_block(lines, "内容契约", template.get("content_contract"))
    append_config_block(lines, "版式结构", template.get("layout"))
    if render_style_profile:
        lines.extend(
            [
                "",
                "【用户确认的 IP 渲染风格】",
                "下方 profile 只覆盖材质、线条、色彩、形状和表面语气。"
                "它不得改变当前业务场景、文字策略、画幅、布局或角色身份；"
                "若基底模板含有旧的表面风格描述，以本 profile 为准。",
            ]
        )
        lines.extend(render_config_value(render_style_profile))
    else:
        append_config_block(lines, "视觉风格", style)
    append_config_block(lines, "参考图复用政策", template.get("reference_policy"))
    append_config_block(lines, "画布稳定性", template.get("canvas"))
    # 下面这组块来自知识卡片类模板;海报/封面模板没有这些字段时自动跳过
    append_config_block(lines, "信息架构", template.get("information_architecture"))
    append_config_block(lines, "内容解析规则", template.get("extraction_rules"))
    append_config_block(lines, "标题区规范", template.get("header"))
    append_config_block(lines, "主体区规范", template.get("body"))
    append_config_block(lines, "页脚区规范", template.get("footer"))
    append_post_component_safe_zones(lines, template.get("post_components"))
    append_config_block(lines, "视觉元素规则", template.get("visual_elements"))
    append_config_block(lines, "稳定生成协议", template.get("stability_protocol"))
    append_config_block(lines, "质量门禁", template.get("quality_gates"))
    append_config_block(lines, "结构化质量门禁", template.get("quality_gates_v2"))
    append_config_block(lines, "文字规则", template.get("copy_rules"))
    append_config_block(lines, "硬性约束", template.get("constraints"))

    if adaptive_layout:
        lines.extend(
            [
                "",
                "【自适应图文混排硬约束】",
                f"- 最终画布: {adaptive_layout.get('size')};布局: {adaptive_layout.get('layout_mode')};版式: {adaptive_layout.get('layout_profile') or 'safe-split'}",
                "- 当前阶段只生成无字主视觉;Image Factory 后处理会将图片裁入 visual 区，再将标题和说明写入彼此分离的文字区。",
                "- 禁止在主视觉中生成汉字、英文、数字、招牌、屏幕文字或伪字形。",
                "- 主体必须完整、不贴边，以便在不同宽高比的 visual 区内安全裁切。",
                "- 图文区域不重叠是硬性条件，不得依赖在图片上加描边字来补救可读性。",
                "- " + layout_profiles.generation_guidance(
                    as_text(adaptive_layout.get("layout_profile")) or "safe-split",
                    layout_mode=as_text(adaptive_layout.get("layout_mode")) or "auto",
                ),
            ]
        )
    elif direct_integrated:
        lines.extend(
            [
                "",
                "【一次生成图文融合硬约束】",
                "- 这是一次生成的正文配图，不进入二次标题带扩展；IP、物件、箭头和少量中文短标注必须组成一个整体画面。",
                "- 只允许少量、短、清晰的中文标题或标签；不要生成长段摘要、密集说明、英文乱码或装饰性伪字。",
                "- 所有文字必须服务于当前判断、流程或生意关系，并与人物动作和物件关系共同表达。",
            ]
        )

    negative_prompt = as_text(template.get("negative_prompt"))
    if negative_prompt:
        lines.extend(["", "【避免】", negative_prompt])

    if adaptive_layout or two_step_publish:
        lines.extend(
            [
                "",
                "请直接生成无字原始主视觉图片。不要输出解释，不要加标题、字幕、标签、logo 或水印。",
            ]
        )
    elif direct_integrated:
        lines.extend(
            [
                "",
                "请直接生成一次性图文融合成品。不要输出解释，不要加无关 logo、水印或额外文案。",
            ]
        )
    elif safe_zone_only_postprocess:
        lines.extend(
            [
                "",
                "请直接生成不含后期安全区组件的原始图片。不要输出解释,不要加英文标题,不要加无关水印。",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "请直接生成成品图片。不要输出解释,不要加英文标题,不要加无关水印。",
            ]
        )
    prompt = "\n".join(lines).rstrip() + "\n"
    if safe_zone_only_postprocess:
        validate_postprocess_prompt(prompt)
    return prompt


def build_manifest(
    template_path: Path,
    template: dict[str, Any],
    brief: dict[str, Any],
    prompt_path: Path,
    brief_path: Path,
    output_dir: Path,
    args: argparse.Namespace,
    size: str,
    item_id: str,
    layout_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content = brief.get("content") if isinstance(brief.get("content"), dict) else {}
    post_components = template.get("post_components") if isinstance(template.get("post_components"), list) else []
    post_assets = brief.get("post_assets") if isinstance(brief.get("post_assets"), dict) else {}
    needs_entry_postprocess = bool(post_components and as_text(post_assets.get("qr_image_path")))
    final_root = "postprocess" if needs_entry_postprocess else "finalize"
    authorized_assets = [
        {
            key: item.get(key)
            for key in ("id", "purpose", "ownership", "path", "required")
            if key in item
        }
        for item in (
            brief.get("visual", {}).get("authorized_assets", [])
            if isinstance(brief.get("visual"), dict)
            else []
        )
        if isinstance(item, dict)
    ]
    reference_plan = reference_strategy_module.compile_reference_plan(
        item_id=item_id,
        template=template,
        brief=brief,
        authorized_assets=authorized_assets,
        prompt_file=prompt_path,
        size=size,
        output_dir=output_dir,
    )
    selected_assets = reference_plan.get("selected_assets", [])
    manifest = {
        "tool": "image-factory",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "compile_only": True,
        "template": {
            "id": template.get("id"),
            "name": template.get("name"),
            "path": str(template_path),
        },
        "brief": {
            "id": brief.get("id"),
            "scene": brief.get("scene"),
            "goal": brief.get("goal"),
            "headline": content.get("headline"),
            "delivery_mode": brief.get("delivery_mode"),
            "style_variant_id": (
                brief.get("visual", {}).get("style_variant_id")
                if isinstance(brief.get("visual"), dict)
                else None
            ),
            "has_source_content": bool(brief.get("source_content")),
            "has_reference_analysis": isinstance(brief.get("reference_analysis"), dict),
        },
        "inputs": {
            "authorized_visual_assets": authorized_assets,
        },
        "reference_plan": reference_plan,
        "brief_file": str(brief_path),
        "size": size,
        "prompt_file": str(prompt_path),
        "output_dir": str(output_dir),
        "expected_outputs": {
            "raw_image": str(output_dir / "image" / f"{item_id}.png"),
            "final_image": str(output_dir / final_root / "final" / f"{item_id}.png"),
        },
        "post_components": post_components,
        "post_assets": post_assets,
        "finalization": {
            "route": "with-entry" if needs_entry_postprocess else "no-entry",
            "entry_component_available": bool(post_components),
            "entry_asset_available": bool(as_text(post_assets.get("qr_image_path"))),
            "entrypoint": "scripts/finalize_knowledge_card.py",
        },
        "postprocess": {
            "enabled": needs_entry_postprocess,
            "contract": "deterministic components only; external assets are supplied by the caller",
        },
        "visual_qa": {
            "required": True,
            "status": "pending",
            "attachment_evidence_is_visual_qa": False,
        },
    }
    if isinstance(brief.get("director"), dict):
        manifest["director_plan"] = brief["director"]
        manifest["director_plan_file"] = str(output_dir / "ip-director-plan.json")
    if reference_plan.get("selection_required"):
        manifest["render_candidates"] = reference_plan.get("candidates", [])
    else:
        manifest["render_handoff"] = render_handoff_module.build_render_handoff(
            item_id=item_id,
            prompt_file=prompt_path,
            size=size,
            output_dir=output_dir / "image",
            assets=selected_assets,
        )
    if layout_plan:
        manifest["layout_plan"] = layout_plan
        manifest["postprocess"]["enabled"] = True
        manifest["postprocess"]["contract"] = (
            "adaptive mixed layout; image and text zones are deterministic and non-overlapping"
        )
        manifest["postprocess"]["entrypoint"] = "scripts/compose_adaptive_mixed_layout.py"
    delivery_mode = as_text(brief.get("delivery_mode"))
    if delivery_mode == delivery_modes.DIRECT_INTEGRATED:
        raw_path = manifest["expected_outputs"]["raw_image"]
        manifest["expected_outputs"]["final_image"] = raw_path
        manifest["delivery"] = {
            "mode": delivery_mode,
            "operation_count": 1,
            "deliverable": "final_image",
            "status": "awaiting_direct_render",
            "text_integrated": True,
            "raw_is_internal_alias": True,
        }
        manifest["postprocess"]["enabled"] = False
        manifest["visual_qa"]["deliverable_under_review"] = "final_image"
        manifest["visual_qa"]["required_checks"] = [
            "ip_identity",
            "semantic_action",
            "integrated_text_present",
            "integrated_text_legible",
            "text_does_not_overlap_subject",
        ]
        manifest["finalization"] = {
            "route": "direct-integrated",
            "entrypoint": None,
            "contract": "one-pass render contains the IP scene and short visual text; no second compositor stage",
        }
    elif delivery_mode == delivery_modes.TWO_STEP_PUBLISH:
        publish_preset = as_text(brief.get("composition", {}).get("publish_preset")) or "square_1_1"
        publish_layout = {
            "schema_version": "image-publish-layout/v1",
            "id": f"{item_id}-publish-layout",
            "preset": publish_preset,
            "layout_profile": "title-band-top",
            "source_image": manifest["expected_outputs"]["raw_image"],
            "output_image": manifest["expected_outputs"]["final_image"],
            "title": {
                "kicker": content.get("subheadline") or "",
                "headline": content.get("headline") or "",
                "support": content.get("summary") or "",
                "footer": content.get("brand") or "",
            },
        }
        publish_extension = as_text(brief.get("composition", {}).get("publish_extension"))
        if publish_extension:
            publish_layout["extension_id"] = publish_extension
        manifest["publish_layout"] = publish_layout
        manifest["delivery"] = {
            "mode": delivery_mode,
            "operation_count": 2,
            "deliverable": "final_image",
            "status": "awaiting_raw_render_then_publish_layout",
            "raw_publishable": False,
            "raw_retained_as": "technical_sidecar",
        }
        manifest["postprocess"]["enabled"] = True
        manifest["postprocess"]["entrypoint"] = "skills/image-publish-layout-factory/scripts/compose_publish_layout.py"
        manifest["postprocess"]["contract"] = "raw render followed by image-publish-layout/v1; raw must not be overwritten or published as final"
        manifest["visual_qa"]["deliverable_under_review"] = "final_image"
        manifest["visual_qa"]["required_checks"] = [
            "raw_has_no_text",
            "final_title_band_present",
            "final_title_legible",
            "final_text_does_not_overlap_visual",
            "raw_not_published_as_final",
        ]
        manifest["finalization"] = {
            "route": "two-step-publish",
            "entrypoint": "skills/image-publish-layout-factory/scripts/compose_publish_layout.py",
            "contract": "two operations inside one user request; only final_image is the user-facing result",
        }
    return manifest


def list_canvas_presets_command() -> int:
    print("=== 可选画布预设 ===")
    for preset in canvas_presets.list_presets():
        aliases = "/".join(preset.get("aliases", []))
        print(
            f"- {preset['id']}  {preset['display_name']}  size={preset['size']}"
            + (f"  aliases={aliases}" if aliases else "")
        )
    print("- custom  自定义尺寸  使用 --size 宽x高")
    return 0


def list_layout_profiles_command() -> int:
    print("=== 可选图文版式 ===")
    for profile in layout_profiles.list_profiles():
        aliases = "/".join(profile.get("aliases", []))
        marker = " ★默认" if profile.get("default") else ""
        print(f"- {profile['id']}  {profile['display_name']}{marker}  aliases={aliases}")
    return 0


def list_delivery_modes_command() -> int:
    print("=== IP 文章配图交付模式（必须选择，不设默认） ===")
    for mode in delivery_modes.list_modes():
        print(f"- {mode['id']}  {mode['display_name']}  {mode['description']}")
    return 0


def collection_title_from_args(args: argparse.Namespace, brief: dict[str, Any], item_id: str) -> str:
    explicit = as_text(getattr(args, "publish_slug", ""))
    if explicit:
        return explicit
    source = brief.get("source") if isinstance(brief.get("source"), dict) else {}
    source_file = as_text(source.get("source_content_file")) or as_text(getattr(args, "content_file", ""))
    if source_file:
        source_path = Path(source_file).expanduser()
        if source_path.parent.name:
            return source_path.parent.name
    content = brief.get("content") if isinstance(brief.get("content"), dict) else {}
    return as_text(content.get("headline")) or item_id


def list_templates_command(repo_root: Path) -> int:
    """打印 registry 发现的全部模板,方便用户选择模板 id。"""
    entries = registry.list_templates(repo_root)
    if not entries:
        print("没有发现任何模板。请检查 templates/ 与 skills/*/templates/。")
        return 1
    for entry in entries:
        parts = [str(entry["id"])]
        if entry.get("scene"):
            scene_label = f"scene={entry['scene']}"
            if entry.get("default_for_scene"):
                scene_label += "(默认)"
            parts.append(scene_label)
        if entry.get("size"):
            parts.append(f"size={entry['size']}")
        if entry.get("name"):
            parts.append(str(entry["name"]))
        if entry.get("aliases"):
            # 业务别名:调用方可直接 --template <别名> 选模板(如 暖纸手绘金句引流卡)
            parts.append(f"别名={'/'.join(entry['aliases'])}")
        if entry.get("error"):
            parts.append(f"⚠ {entry['error']}")
        try:
            location = Path(entry["path"]).relative_to(repo_root)
        except ValueError:
            location = entry["path"]
        parts.append(str(location))
        print("  ".join(parts))
    return 0


def list_routes_command(repo_root: Path) -> int:
    """打印业务路由表 + 横向正交能力清单,方便调用方按业务类型选模板。

    数据源是 scripts/image_route_map.json;json 缺失或损坏时回退到硬编码 fallback,
    永远保证主 SKILL.md 路由不失效。
    """
    route_map = registry.load_route_map(repo_root)
    businesses = route_map.get("businesses", [])
    capabilities = route_map.get("cross_cutting_capabilities", [])

    if not businesses:
        print("没有业务路由数据。请检查 scripts/image_route_map.json。")
        return 1

    print("=== 业务类型路由 ===")
    for business in businesses:
        bid = business.get("business_id", "?")
        name = business.get("display_name", "")
        shell = business.get("shell_skill", "")
        scene = business.get("scene", "-")
        aliases = "/".join(business.get("trigger_aliases", []))
        print(f"\n[{bid}] {name}  shell={shell}  scene={scene}")
        if aliases:
            print(f"  别名: {aliases}")
        if business.get("requires_reference"):
            print(f"  ⚠ 需要 reference_analysis.json")
        templates = business.get("templates", [])
        for tpl in templates:
            tid = tpl.get("id", "?")
            tname = tpl.get("display_name", "")
            status = tpl.get("status", "?")
            tags = "/".join(tpl.get("tags", []))
            marker = ""
            if tpl.get("default"):
                marker += " ★默认"
            if status != "production":
                marker += f" [{status}]"
            tag_part = f"  tags={tags}" if tags else ""
            print(f"  - {tid}  {tname}{marker}{tag_part}")

    print("\n=== 横向正交能力(任何业务可开启) ===")
    for cap in capabilities:
        cid = cap.get("id", "?")
        cname = cap.get("display_name", "")
        applies = cap.get("applies_to", "all")
        applies_str = "all" if applies == "all" else "/".join(applies)
        status = cap.get("status", "?")
        marker = f" [{status}]" if status != "production" else ""
        print(f"  - [{cid}] {cname}  applies_to={applies_str}{marker}")

    return 0


def run(args: argparse.Namespace) -> int:
    repo_root = repo_root_from_script()
    if args.list_templates:
        return list_templates_command(repo_root)
    if args.list_routes:
        return list_routes_command(repo_root)
    if args.list_canvas_presets:
        return list_canvas_presets_command()
    if args.list_layout_profiles:
        return list_layout_profiles_command()
    if args.list_delivery_modes:
        return list_delivery_modes_command()

    scene_for_template = args.scene or DEFAULT_SCENE
    template_value = args.template
    if args.brief:
        initial_brief = load_json(Path(args.brief).expanduser(), "图像 brief")
        if card_brief_module.is_card_brief(initial_brief):
            # 知识卡片 brief: 场景固定为 knowledge_card,模板优先用 brief 里声明的 template_id
            scene_for_template = args.scene or card_brief_module.SCENE_KNOWLEDGE_CARD
            if not template_value:
                template_value = as_text(initial_brief.get("template_id")) or None
        elif not args.template and not args.scene:
            scene_for_template = as_text(initial_brief.get("scene")) or DEFAULT_SCENE
    template_path = resolve_template(template_value, scene_for_template)
    template = load_json(template_path, "模板")
    if template.get("id") == "poster-reference-remix-v1" and not args.reference_analysis:
        raise ImageFactoryError("Reference remix requires --reference-analysis from authorized reference analysis.")
    apply_template_overrides(template, args.set)
    brief = build_brief(args, template)
    if brief.get("scene") not in {"poster", "marketing_poster", "product_main", "product_detail"}:
        raise ImageFactoryError("This public edition supports product posters only.")
    ip_style_variants.validate_template_scene(brief, template)
    delivery_mode = delivery_modes.resolve(
        as_text(brief.get("scene")) or scene_for_template,
        getattr(args, "delivery_mode", ""),
        brief.get("delivery_mode"),
    )
    delivery_modes.require_confirmed_selection(
        as_text(brief.get("scene")) or scene_for_template,
        brief,
    )
    if delivery_mode:
        brief["delivery_mode"] = delivery_mode
    render_style_profile = ip_style_variants.apply_selection(repo_root, brief)
    composition = brief.get("composition") if isinstance(brief.get("composition"), dict) else {}
    arg_preset = as_text(getattr(args, "canvas_preset", ""))
    arg_size = as_text(getattr(args, "size", ""))
    if arg_preset and arg_size:
        raise ImageFactoryError("--canvas-preset 与 --size 不能同时使用")
    if arg_preset:
        requested_preset, requested_size = arg_preset, ""
    elif arg_size:
        requested_preset, requested_size = "", arg_size
    else:
        requested_preset = as_text(composition.get("canvas_preset"))
        requested_size = as_text(composition.get("size"))
        if not requested_preset and not requested_size:
            requested_preset, requested_size = (
                ip_style_variants.canvas_request_if_template_mismatches(
                    brief,
                    as_text(template.get("size")) or DEFAULT_SIZE,
                )
            )
    layout_mode = as_text(getattr(args, "layout_mode", "")) or as_text(composition.get("layout_mode")) or "auto"
    layout_profile = as_text(getattr(args, "layout_profile", "")) or as_text(composition.get("layout_profile")) or "safe-split"
    if (getattr(args, "adaptive_layout", False) or getattr(args, "layout_mode", "")
            or getattr(args, "layout_profile", "") or composition.get("adaptive_layout")
            or composition.get("layout_mode") or composition.get("layout_profile")):
        raise ImageFactoryError("Public poster edition uses integrated text; adaptive mixed-layout is not supported.")
    adaptive_layout_requested = False
    layout_plan: dict[str, Any] | None = None
    if adaptive_layout_requested:
        resolved_preset, size, layout_plan = canvas_presets.resolve_canvas(
            requested_preset=requested_preset,
            requested_size=requested_size,
            fallback_size=as_text(template.get("size")) or DEFAULT_SIZE,
            layout_mode=layout_mode,
            layout_profile=layout_profile,
        )
        composition.update(
            {
                "adaptive_layout": True,
                "canvas_preset": resolved_preset,
                "size": size,
                "layout_mode": layout_plan["layout_mode"],
                "layout_profile": layout_plan["layout_profile"],
                "layout_contract": layout_plan,
            }
        )
        brief["composition"] = composition
    elif requested_preset or requested_size:
        resolved_preset, size, _ = canvas_presets.resolve_canvas(
            requested_preset=requested_preset,
            requested_size=requested_size,
            fallback_size=as_text(template.get("size")) or DEFAULT_SIZE,
            layout_mode=layout_mode,
            layout_profile=layout_profile,
        )
        composition.update({"canvas_preset": resolved_preset, "size": size})
        brief["composition"] = composition
    else:
        size = arg_size or as_text(composition.get("size")) or as_text(template.get("size")) or DEFAULT_SIZE
    ip_style_variants.validate_canvas_selection(brief, size)
    item_id = slugify(args.id or as_text(brief.get("id")) or as_text(brief.get("content", {}).get("headline")))

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
        if not output_dir.is_absolute():
            output_dir = (Path.cwd() / output_dir).resolve()
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = repo_root / "outputs" / "image-assets" / f"{stamp}-{item_id}"
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ImageFactoryError("Output directory is not empty; choose a new version directory.")
    output_dir.mkdir(parents=True, exist_ok=True)

    brief_path = output_dir / "image_brief.json"
    write_json(brief_path, brief)
    if isinstance(brief.get("director"), dict):
        write_json(output_dir / "ip-director-plan.json", brief["director"])

    prompt = compile_prompt(
        template,
        brief,
        size,
        render_style_profile=render_style_profile,
    )
    prompt_path = output_dir / f"{item_id}.prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    manifest = build_manifest(
        template_path,
        template,
        brief,
        prompt_path,
        brief_path,
        output_dir,
        args,
        size,
        item_id,
        layout_plan,
    )
    manifest.pop("finalization", None)
    manifest["public_edition"] = "image-factory-public/v1"
    if not layout_plan:
        manifest["expected_outputs"]["final_image"] = manifest["expected_outputs"]["raw_image"]
        manifest["postprocess"] = {"required": False}
    manifest_path = output_dir / "image-asset-manifest.json"
    print(f"template: {template_path}")
    print(f"brief: {brief_path}")
    print(f"prompt: {prompt_path}")
    print(f"manifest: {manifest_path}")
    print(f"size: {size}")
    print("mode: compile-only; render-handoff: image-render-handoff/v1")

    if args.print_prompt:
        print("")
        print(prompt)

    if args.print_brief:
        print("")
        print(json.dumps(brief, ensure_ascii=False, indent=2))

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile one image asset request without calling an image API.")
    parser.add_argument("--brief", help="Structured image_brief JSON path")
    parser.add_argument("--template", help="Template id or JSON path; default is inferred from --scene")
    parser.add_argument("--content-file", help="Raw source content file to parse into image_brief")
    parser.add_argument(
        "--source-max-chars",
        type=int,
        default=DEFAULT_SOURCE_MAX_CHARS,
        help=f"Max cleaned source chars embedded in prompt; 0 means no limit. Default {DEFAULT_SOURCE_MAX_CHARS}",
    )
    parser.add_argument("--reference-analysis", help="JSON or markdown file created after analyzing a reference image/poster")
    parser.add_argument("--reference-image", help="Reference image path or label; tracked in brief only, not analyzed by this CLI")
    parser.add_argument(
        "--authorized-asset",
        action="append",
        help="Authorized business asset as purpose=path or JSON object; repeatable",
    )
    parser.add_argument("--scene", help="poster, marketing_poster, article_illustration, cover, wechat_cover, video_cover, knowledge_card, or custom scene; defaults to brief.scene or poster")
    parser.add_argument("--goal", help="Image goal, e.g. lead_generation, explain_concept")
    parser.add_argument("--audience", help="Target audience")
    parser.add_argument("--output-dir", help="Output directory; default outputs/image-assets/<timestamp>-<id>")
    parser.add_argument("--publish-root", help="Override final image asset publish root; default reads IMAGE_FACTORY_PUBLISH_ROOT or .ai-native/profile.json")
    parser.add_argument("--publish-slug", help="Folder name under publish root; default uses source article folder or headline")
    parser.add_argument("--no-publish-assets", action="store_true", help="Do not copy final images into the shallow asset publish folder")
    parser.add_argument("--id", help="Output id / filename prefix")
    parser.add_argument("--size", help="Override template image size")
    parser.add_argument("--canvas-preset", help="Canvas preset id or alias, such as square_1_1, 3:4, 9:16, 16:9, or 公众号头图")
    parser.add_argument("--adaptive-layout", action="store_true", help="Bind a custom --size to deterministic non-overlapping image/text layout")
    parser.add_argument("--layout-mode", choices=["auto", "stacked", "split-left", "banner"], help="Adaptive non-overlapping mixed layout; defaults from aspect ratio")
    parser.add_argument(
        "--layout-profile",
        help="Mixed-layout visual relationship: safe-split, integrated-negative-space, or integrated-scene",
    )
    parser.add_argument(
        "--delivery-mode",
        choices=sorted(delivery_modes.MODE_IDS),
        help="IP article delivery choice; direct-integrated (one pass) or two-step-publish (raw then layout)",
    )
    parser.add_argument("--set", action="append", help="Override template config with dot.path=value, repeatable")
    parser.add_argument("--print-brief", action="store_true", help="Print generated or loaded image_brief JSON to stdout")
    parser.add_argument("--print-prompt", action="store_true", help="Print compiled prompt to stdout")
    parser.add_argument("--list-templates", action="store_true", help="List all templates discovered in templates/ and skills/*/templates/, then exit")
    parser.add_argument("--list-routes", action="store_true", help="List business routes + cross-cutting capabilities from scripts/image_route_map.json, then exit")
    parser.add_argument("--list-canvas-presets", action="store_true", help="List selectable canvas presets and aliases, then exit")
    parser.add_argument("--list-layout-profiles", action="store_true", help="List selectable mixed-layout profiles, then exit")
    parser.add_argument("--list-delivery-modes", action="store_true", help="List explicit IP article delivery modes, then exit")

    parser.add_argument("--title", help="Inline headline when --brief is not used")
    parser.add_argument("--subtitle", help="Inline subheadline")
    parser.add_argument("--summary", help="Inline summary")
    parser.add_argument("--point", action="append", help="Inline point, repeatable")
    parser.add_argument("--cta", help="Inline call to action")
    parser.add_argument("--brand", help="Inline brand or signature")
    parser.add_argument("--visual-subject", help="Main visual subject")
    parser.add_argument("--mood", help="Desired mood")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except ImageFactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
