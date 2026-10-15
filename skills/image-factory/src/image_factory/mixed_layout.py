"""Deterministic image/text composition for adaptive canvas layouts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from image_factory.canvas_presets import validate_zones
from image_factory.errors import ImageFactoryError


DEFAULT_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
)
CANNOT_START_LINE = set("，。！？；：、,.!?;:)]】》」』%％")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _font_path(explicit: str = "") -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path
        raise ImageFactoryError(f"字体不存在：{path}")
    for path in DEFAULT_FONT_CANDIDATES:
        if path.is_file():
            return path
    raise ImageFactoryError("未找到可用中文字体")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def _box_xyxy(box: dict[str, int]) -> tuple[int, int, int, int]:
    return box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]


def _width(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.FreeTypeFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), value, font=font)
    return right - left


def _wrap(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for paragraph in [part.strip() for part in value.splitlines() if part.strip()]:
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and _width(draw, candidate, font) > max_width:
                if char in CANNOT_START_LINE:
                    # 标点不能被单独甩到下一行；暂时留在本行，
                    # _fit_text 会通过降低字号直到这一行真正容得下。
                    lines.append(candidate.rstrip())
                    current = ""
                else:
                    lines.append(current.rstrip())
                    current = char.lstrip()
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
    return lines


def _fit_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    font_path: Path,
    box: dict[str, int],
    *,
    max_size: int,
    min_size: int,
    max_lines: int,
    line_gap_ratio: float = 0.22,
    avoid_orphan_line: bool = False,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    pad_x = max(12, round(box["w"] * 0.025))
    pad_y = max(10, round(box["h"] * 0.06))
    max_width = box["w"] - pad_x * 2
    max_height = box["h"] - pad_y * 2
    for size in range(max_size, min_size - 1, -2):
        font = _font(font_path, size)
        lines = _wrap(draw, value, font, max_width)
        line_gap = max(8, round(size * line_gap_ratio))
        height = len(lines) * size + max(0, len(lines) - 1) * line_gap
        orphan_free = (
            not avoid_orphan_line
            or len(lines) <= 1
            or _width(draw, lines[-1], font) >= round(max_width * 0.45)
        )
        if (
            lines
            and len(lines) <= max_lines
            and height <= max_height
            and orphan_free
            and all(_width(draw, line, font) <= max_width for line in lines)
            and all(line[0] not in CANNOT_START_LINE for line in lines if line)
        ):
            return font, lines, line_gap
    raise ImageFactoryError(
        f"文字无法在最小字号 {min_size}px 下放入安全区，请缩短文案：{value}"
    )


def _draw_text_zone(
    draw: ImageDraw.ImageDraw,
    value: str,
    font_path: Path,
    zone_name: str,
    box: dict[str, int],
    *,
    max_size: int,
    min_size: int,
    max_lines: int,
    fill: str,
    accent: bool = False,
    avoid_orphan_line: bool = False,
) -> dict[str, Any] | None:
    if not value:
        return None
    font, lines, line_gap = _fit_text(
        draw,
        value,
        font_path,
        box,
        max_size=max_size,
        min_size=min_size,
        max_lines=max_lines,
        avoid_orphan_line=avoid_orphan_line,
    )
    pad_x = max(12, round(box["w"] * 0.025))
    total_height = len(lines) * font.size + max(0, len(lines) - 1) * line_gap
    x = box["x"] + pad_x
    y = box["y"] + max(0, (box["h"] - total_height) // 2)
    line_boxes: list[list[int]] = []
    for line in lines:
        text_box = draw.textbbox((x, y), line, font=font)
        draw.text((x, y), line, font=font, fill=fill)
        line_boxes.append(list(text_box))
        y += font.size + line_gap
    if accent:
        accent_y = min(box["y"] + box["h"] - 8, y + 4)
        accent_width = min(round(box["w"] * 0.55), max(80, max(item[2] - item[0] for item in line_boxes)))
        draw.rounded_rectangle((x, accent_y, x + accent_width, accent_y + 7), radius=3, fill="#E65345")
    result = {
        "zone": zone_name,
        "text": value,
        "font_size": font.size,
        "lines": lines,
        "line_boxes_px": line_boxes,
        "zone_box_px": list(_box_xyxy(box)),
    }
    zx1, zy1, zx2, zy2 = _box_xyxy(box)
    for text_box in line_boxes:
        tx1, ty1, tx2, ty2 = text_box
        if tx1 < zx1 or ty1 < zy1 or tx2 > zx2 or ty2 > zy2:
            raise ImageFactoryError(f"文字越出 {zone_name} 安全区：{text_box}")
    return result


def paste_rounded_card(canvas: Image.Image, source: Image.Image, box: dict[str, int], treatment: dict[str, Any]) -> dict[str, Any]:
    target_size = (box["w"], box["h"])
    fitted = ImageOps.fit(source.convert("RGB"), target_size, method=Image.Resampling.LANCZOS)
    radius = max(18, round(min(target_size) * 0.035))
    mask = Image.new("L", target_size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, target_size[0], target_size[1]), radius=radius, fill=255)
    shadow = Image.new("RGB", target_size, "#DDD5CA")
    canvas.paste(shadow, (box["x"] + 6, box["y"] + 8), mask)
    canvas.paste(fitted, (box["x"], box["y"]), mask)
    return {
        "zone": "visual",
        "source_size": list(source.size),
        "box_px": list(_box_xyxy(box)),
        "fit": "cover",
        "corner_radius_px": radius,
        "renderer": "rounded-card",
    }


def paste_edge_blend(canvas: Image.Image, source: Image.Image, box: dict[str, int], treatment: dict[str, Any]) -> dict[str, Any]:
    target_size = (box["w"], box["h"])
    fitted = ImageOps.fit(source.convert("RGB"), target_size, method=Image.Resampling.LANCZOS)
    mask = Image.new("L", target_size, 255)
    pixels = mask.load()
    source_pixels = fitted.load()
    edge = str(treatment.get("blend_edge") or "left")
    feather = max(1, min(int(treatment.get("feather_px") or 1), max(target_size)))
    for y in range(target_size[1]):
        for x in range(target_size[0]):
            red, green, blue = source_pixels[x, y]
            # 纯白/近白背景是线稿常见的素材底。融合版式把它确定性地转成透明，
            # 让统一纸色穿透；较深或有色画面保持不透明，只在邻近文字的一侧羽化。
            background_alpha = max(0, min(255, round((255 - min(red, green, blue)) / 18 * 255)))
            pixels[x, y] = min(pixels[x, y], background_alpha)
            if edge == "left" and x < feather:
                pixels[x, y] = min(pixels[x, y], round(255 * x / feather))
            elif edge == "right" and x >= target_size[0] - feather:
                pixels[x, y] = min(pixels[x, y], round(255 * (target_size[0] - 1 - x) / feather))
            elif edge == "top" and y < feather:
                pixels[x, y] = min(pixels[x, y], round(255 * y / feather))
            elif edge == "bottom" and y >= target_size[1] - feather:
                pixels[x, y] = min(pixels[x, y], round(255 * (target_size[1] - 1 - y) / feather))
    canvas.paste(fitted, (box["x"], box["y"]), mask)
    return {
        "zone": "visual",
        "source_size": list(source.size),
        "box_px": list(_box_xyxy(box)),
        "fit": "cover",
        "corner_radius_px": 0,
        "renderer": "edge-blend",
        "blend_edge": edge,
        "feather_px": feather,
    }


def paste_scene_pocket(canvas: Image.Image, source: Image.Image, box: dict[str, int], treatment: dict[str, Any]) -> dict[str, Any]:
    """Paste a full-canvas scene, then carve a soft deterministic text island."""

    target_size = (canvas.width, canvas.height)
    fitted = ImageOps.fit(source.convert("RGB"), target_size, method=Image.Resampling.LANCZOS)
    canvas.paste(fitted, (0, 0))

    anchor = str(treatment.get("text_island_anchor") or "left")
    if anchor == "top":
        island = (
            round(canvas.width * 0.035),
            round(canvas.height * 0.025),
            round(canvas.width * 0.965),
            round(canvas.height * 0.47),
        )
    else:
        island = (
            round(canvas.width * 0.025),
            round(canvas.height * 0.025),
            round(canvas.width * 0.50),
            round(canvas.height * 0.68),
        )
    mask = Image.new("L", target_size, 0)
    mask_draw = ImageDraw.Draw(mask)
    radius = max(40, round(min(canvas.width, canvas.height) * 0.08))
    opacity = max(0, min(255, int(treatment.get("paper_opacity") or 244)))
    mask_draw.rounded_rectangle(island, radius=radius, fill=opacity)

    # Two soft lobes break the rectangular silhouette without depending on
    # image-model compliance.  The text itself remains inside rectangular
    # glyph-safe zones contained by this larger organic island.
    ix1, iy1, ix2, iy2 = island
    lobe = max(24, round(min(canvas.width, canvas.height) * 0.08))
    if anchor == "top":
        mask_draw.ellipse((ix1 + lobe, iy2 - lobe, ix1 + lobe * 3, iy2 + lobe), fill=opacity)
        mask_draw.ellipse((ix2 - lobe * 3, iy2 - lobe, ix2 - lobe, iy2 + lobe), fill=opacity)
    else:
        mask_draw.ellipse((ix2 - lobe, iy1 + lobe, ix2 + lobe, iy1 + lobe * 3), fill=opacity)
        mask_draw.ellipse((ix2 - lobe, iy2 - lobe * 3, ix2 + lobe, iy2 - lobe), fill=opacity)
    feather = max(1, int(treatment.get("feather_px") or 1))
    mask = mask.filter(ImageFilter.GaussianBlur(radius=feather))
    paper = Image.new("RGB", target_size, str(treatment.get("paper_color") or "#F7F2E9"))
    canvas.paste(paper, (0, 0), mask)
    return {
        "zone": "visual",
        "source_size": list(source.size),
        "box_px": [0, 0, canvas.width, canvas.height],
        "fit": "cover",
        "corner_radius_px": 0,
        "renderer": "scene-pocket",
        "scene_box": "full-canvas",
        "text_island_anchor": anchor,
        "text_island_box_px": list(island),
        "feather_px": feather,
    }


VISUAL_RENDERERS = {
    "rounded-card": paste_rounded_card,
    "edge-blend": paste_edge_blend,
    "scene-pocket": paste_scene_pocket,
}


def compose(
    *,
    source_image: Path,
    brief: dict[str, Any],
    layout_plan: dict[str, Any],
    output_path: Path,
    font_path: str = "",
) -> dict[str, Any]:
    if layout_plan.get("schema_version") != "adaptive-mixed-layout/v1":
        raise ImageFactoryError("layout_plan.schema_version 必须为 adaptive-mixed-layout/v1")
    width, height = int(layout_plan["width"]), int(layout_plan["height"])
    zones = layout_plan.get("zones")
    if not isinstance(zones, dict):
        raise ImageFactoryError("layout_plan.zones 必须为 object")
    constraints = layout_plan.get("constraints") if isinstance(layout_plan.get("constraints"), dict) else {}
    allow_visual_overlap = bool(constraints.get("full_canvas_scene"))
    validate_zones(zones, width, height, allow_visual_overlap=allow_visual_overlap)
    if not source_image.is_file():
        raise ImageFactoryError(f"原始图片不存在：{source_image}")

    content = brief.get("content") if isinstance(brief.get("content"), dict) else {}
    headline = _text(content.get("headline"))
    if not headline:
        raise ImageFactoryError("brief.content.headline 不能为空")
    support = _text(content.get("subheadline")) or _text(content.get("summary"))
    if not support:
        points = content.get("points") if isinstance(content.get("points"), list) else []
        support = " · ".join(_text(item) for item in points[:3] if _text(item))
    header = _text(content.get("brand"))
    footer = _text(content.get("cta"))

    canvas = Image.new("RGB", (width, height), "#F7F2E9")
    draw = ImageDraw.Draw(canvas)
    font = _font_path(font_path)
    treatment = layout_plan.get("visual_treatment") if isinstance(layout_plan.get("visual_treatment"), dict) else {
        "renderer": "rounded-card",
        "shadow": True,
    }
    renderer_id = _text(treatment.get("renderer")) or "rounded-card"
    renderer = VISUAL_RENDERERS.get(renderer_id)
    if renderer is None:
        raise ImageFactoryError(f"未知视觉渲染器：{renderer_id}")
    visual_result = renderer(canvas, Image.open(source_image), zones["visual"], treatment)
    typography = layout_plan.get("typography") if isinstance(layout_plan.get("typography"), dict) else {}
    min_dim = min(width, height)
    text_results = [
        _draw_text_zone(
            draw,
            header,
            font,
            "header",
            zones["header"],
            max_size=max(30, round(min_dim * 0.032)),
            min_size=int(typography.get("header_min_px") or 22),
            max_lines=1,
            fill="#4B79A6",
        ),
        _draw_text_zone(
            draw,
            headline,
            font,
            "headline",
            zones["headline"],
            max_size=max(64, round(min_dim * 0.092)),
            min_size=int(typography.get("headline_min_px") or 34),
            max_lines=3,
            fill="#111111",
            accent=True,
            avoid_orphan_line=True,
        ),
        _draw_text_zone(
            draw,
            support,
            font,
            "support",
            zones["support"],
            max_size=max(38, round(min_dim * 0.043)),
            min_size=int(typography.get("support_min_px") or 24),
            max_lines=3,
            fill="#4D4A47",
        ),
        _draw_text_zone(
            draw,
            footer,
            font,
            "footer",
            zones["footer"],
            max_size=max(28, round(min_dim * 0.032)),
            min_size=int(typography.get("header_min_px") or 22),
            max_lines=1,
            fill="#6E93B7",
        ),
    ]
    rendered_text = [item for item in text_results if item]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="PNG")
    quality_gates: dict[str, Any] = {
        "zone_overlap": False,
        "text_outside_zone": False,
        "minimum_font_size_respected": True,
    }
    if allow_visual_overlap:
        quality_gates.update(
            {
                "zone_overlap": "visual_overlap_masked_by_text_island",
                "full_canvas_scene": True,
                "text_island_background_deterministic": bool(
                    constraints.get("text_island_background_deterministic")
                ),
            }
        )
    return {
        "schema_version": "adaptive-mixed-layout-result/v1",
        "output_image": str(output_path.resolve()),
        "size": f"{width}x{height}",
        "layout_mode": layout_plan.get("layout_mode"),
        "layout_profile": layout_plan.get("layout_profile") or "safe-split",
        "visual": visual_result,
        "text": rendered_text,
        "quality_gates": quality_gates,
    }


def compose_from_manifest(
    *,
    manifest_path: Path,
    source_image: Path,
    output_path: Path,
    font_path: str = "",
) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layout_plan = manifest.get("layout_plan")
    if not isinstance(layout_plan, dict):
        raise ImageFactoryError("编译 manifest 不包含 layout_plan，请使用 --canvas-preset 或 --size")
    brief_path = Path(_text(manifest.get("brief_file"))).expanduser()
    if not brief_path.is_absolute():
        brief_path = (manifest_path.parent / brief_path).resolve()
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    result = compose(
        source_image=source_image,
        brief=brief,
        layout_plan=layout_plan,
        output_path=output_path,
        font_path=font_path,
    )
    result_path = output_path.with_suffix(".layout-result.json")
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result_path
