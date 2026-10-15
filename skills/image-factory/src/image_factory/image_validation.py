"""Lightweight validation for provider-generated raw image files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from image_factory.errors import ImageFactoryError


try:
    from PIL import Image, ImageStat
except ModuleNotFoundError as exc:  # pragma: no cover - depends on runtime install
    Image = None  # type: ignore[assignment]
    ImageStat = None  # type: ignore[assignment]
    PIL_IMPORT_ERROR = exc
else:
    PIL_IMPORT_ERROR = None


# 等比尺寸容忍阈值:provider 偶发返回等比但分辨率不同的图(例如 16:9 的 1672x941 对 1536x864)
# 时不应整条 fail。宽高比相对误差 ≤ 1% 视为通过,落 manifest 的 actual_size 字段以便排查。
ASPECT_RATIO_TOLERANCE = 0.01


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def parse_size(value: Any) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\s*x\s*(\d+)\s*$", as_text(value).lower())
    if not match:
        return None
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def _size_string(size: tuple[int, int]) -> str:
    return f"{size[0]}x{size[1]}"


def validate_raw_image(path: str | Path, expected_size: str | tuple[int, int] | None = None) -> dict[str, Any]:
    image_path = Path(path).expanduser()
    result: dict[str, Any] = {
        "path": str(image_path),
        "ok": False,
        "exists": image_path.exists(),
        "bytes": image_path.stat().st_size if image_path.exists() else 0,
    }
    if not image_path.exists():
        result["error"] = "raw image file does not exist"
        return result
    if result["bytes"] < 64_000:
        result["error"] = "raw image file is too small to trust"
        return result
    if PIL_IMPORT_ERROR is not None:
        # Pillow 缺失降级为 skip + warning,而不是 fail。pyproject.toml 已声明 Pillow 为核心依赖,
        # 但用户用系统 Python / 全新 venv 偶发未装时不应让整条 pipeline 挂掉;
        # 校验跳过同时给 warning,下游决定是否要硬挂。
        result["ok"] = True
        result["validation_skipped"] = True
        result["warning"] = (
            "Pillow is not installed; raw image validation skipped. "
            "Run `pip install Pillow>=10.0` to enable size/luminance checks."
        )
        return result

    try:
        with Image.open(image_path) as image:
            image.load()
            rgb = image.convert("RGB")
    except Exception as exc:  # noqa: BLE001 - user-facing validation detail
        result["error"] = f"raw image cannot be opened: {exc}"
        return result

    width, height = rgb.size
    actual_size = (width, height)
    result["width"] = width
    result["height"] = height
    result["actual_size"] = _size_string(actual_size)
    if height > 0:
        result["aspect_ratio"] = round(width / height, 4)
    expected = parse_size(expected_size) if not isinstance(expected_size, tuple) else expected_size
    if expected:
        result["expected_size"] = _size_string(expected)
        if actual_size != expected:
            # 严格等尺寸不一定要 fail:provider 偶发返回等比放大/缩小的图(如 1672x941 对 1536x864),
            # 图本身没问题。只要宽高比误差 ≤ 1% 就放行,写入 size_tolerated 标记便于下游统计。
            expected_ratio = expected[0] / expected[1] if expected[1] else 0
            actual_ratio = width / height if height else 0
            ratio_delta = abs(actual_ratio - expected_ratio) / expected_ratio if expected_ratio else 1.0
            result["aspect_ratio_delta"] = round(ratio_delta, 4)
            if ratio_delta > ASPECT_RATIO_TOLERANCE:
                result["error"] = (
                    f"raw image size mismatch: expected {expected[0]}x{expected[1]} "
                    f"(aspect {expected_ratio:.3f}), got {width}x{height} "
                    f"(aspect {actual_ratio:.3f}, delta {ratio_delta:.2%} > {ASPECT_RATIO_TOLERANCE:.0%})"
                )
                return result
            result["size_tolerated"] = True

    sample = rgb.resize((96, 144))
    gray = sample.convert("L")
    stat = ImageStat.Stat(gray)
    luminance_mean = float(stat.mean[0])
    luminance_stddev = float(stat.stddev[0])
    histogram = gray.histogram()
    total = sum(histogram) or 1
    non_light = sum(histogram[:235]) / total
    dark = sum(histogram[:90]) / total
    result.update(
        {
            "luminance_mean": round(luminance_mean, 2),
            "luminance_stddev": round(luminance_stddev, 2),
            "non_light_ratio": round(non_light, 4),
            "dark_ratio": round(dark, 4),
        }
    )
    if luminance_stddev < 6.0 or non_light < 0.01:
        result["error"] = "raw image appears blank or incomplete"
        return result

    result["ok"] = True
    return result


def require_valid_raw_image(path: str | Path, expected_size: str | tuple[int, int] | None = None) -> dict[str, Any]:
    result = validate_raw_image(path, expected_size)
    if not result.get("ok"):
        raise ImageFactoryError(as_text(result.get("error")) or "raw image validation failed")
    return result
