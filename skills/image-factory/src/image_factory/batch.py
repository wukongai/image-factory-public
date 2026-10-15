#!/usr/bin/env python3
"""批量 manifest 层:把一组图像任务批量编译为 prompt 与交接清单。

典型用途:训练营一关 7 个知识点,写一个 image-batch/v1 manifest,
一条命令批量产出全部 brief、prompt 与共享 render handoff。

设计要点:
- 每个 item 等价于一次 generate_image_asset.py 调用,字段名与 CLI 参数一一对应,
  不引入第二套语义;defaults 提供公共默认值,item 自身字段优先
- 单个 item 失败不中断整批,失败原因记入批量 manifest,退出码非 0 提示有失败
- 安全红线:本模块不提供远程执行控制参数,不调用任何图片 API
- item 内的相对路径(brief/content_file/reference_analysis)相对批量文件所在目录解析,
  manifest 可以和素材放在一起整体移动
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    from image_factory import cli
    from image_factory import content_outline
    from image_factory.errors import ImageFactoryError
except ModuleNotFoundError:  # pragma: no cover - 仅脚本式加载时触发
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from image_factory import cli
    from image_factory import content_outline
    from image_factory.errors import ImageFactoryError


BATCH_SCHEMA_VERSION = "image-batch/v1"
CONTENT_BATCH_SCHEMA_VERSION = content_outline.CONTENT_SCHEMA_VERSION

# item 字段 → CLI 参数的映射;新增透传字段时只需要在这里登记
ITEM_STRING_FLAGS = {
    "brief": "--brief",
    "template": "--template",
    "content_file": "--content-file",
    "reference_analysis": "--reference-analysis",
    "reference_image": "--reference-image",
    "scene": "--scene",
    "goal": "--goal",
    "audience": "--audience",
    "id": "--id",
    "size": "--size",
    "canvas_preset": "--canvas-preset",
    "layout_mode": "--layout-mode",
    "layout_profile": "--layout-profile",
    "delivery_mode": "--delivery-mode",
    "title": "--title",
    "subtitle": "--subtitle",
    "summary": "--summary",
    "cta": "--cta",
    "brand": "--brand",
    "visual_subject": "--visual-subject",
    "mood": "--mood",
}
ITEM_LIST_FLAGS = {
    "points": "--point",
    "set": "--set",
}
ITEM_INT_FLAGS = {
    "source_max_chars": "--source-max-chars",
}
ITEM_BOOL_FLAGS = {
    "adaptive_layout": "--adaptive-layout",
}
# 这些字段是路径,需要相对批量文件所在目录解析
ITEM_PATH_FIELDS = {"brief", "content_file", "reference_analysis"}
ALLOWED_ITEM_FIELDS = (
    set(ITEM_STRING_FLAGS) | set(ITEM_LIST_FLAGS) | set(ITEM_INT_FLAGS) | set(ITEM_BOOL_FLAGS)
)
ALLOWED_ITEM_FIELDS.add("inline_brief")


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def load_batch(path: Path) -> dict[str, Any]:
    batch = cli.load_json(path, "批量 manifest")
    if as_text(batch.get("schema_version")) == CONTENT_BATCH_SCHEMA_VERSION:
        try:
            return content_outline.to_image_batch(batch)
        except ValueError as exc:
            raise ImageFactoryError(str(exc)) from exc
    if as_text(batch.get("schema_version")) != BATCH_SCHEMA_VERSION:
        raise ImageFactoryError(f"批量 manifest 的 schema_version 必须是 {BATCH_SCHEMA_VERSION!r}: {path}")
    items = batch.get("items")
    if not isinstance(items, list) or not items:
        raise ImageFactoryError(f"批量 manifest 的 items 必须是非空数组: {path}")
    return batch


def merge_item(defaults: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """defaults 提供公共默认值,item 自身字段优先。"""
    merged = dict(defaults)
    merged.update(item)
    return merged


def item_to_argv(merged: dict[str, Any], batch_dir: Path, item_output_dir: Path) -> list[str]:
    """把 item 字段翻译成 generate_image_asset 的命令行参数列表。"""
    unknown = sorted(set(merged) - ALLOWED_ITEM_FIELDS)
    if unknown:
        allowed = ", ".join(sorted(ALLOWED_ITEM_FIELDS))
        raise ImageFactoryError(f"item 含不支持的字段 {unknown},可用字段: {allowed}")

    argv: list[str] = []
    for key, flag in ITEM_STRING_FLAGS.items():
        value = as_text(merged.get(key))
        if not value:
            continue
        if key in ITEM_PATH_FIELDS:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = (batch_dir / path).resolve()
            value = str(path)
        argv.extend([flag, value])
    for key, flag in ITEM_LIST_FLAGS.items():
        values = merged.get(key)
        if isinstance(values, list):
            for value in values:
                if as_text(value):
                    argv.extend([flag, as_text(value)])
        elif as_text(values):
            argv.extend([flag, as_text(values)])
    for key, flag in ITEM_INT_FLAGS.items():
        if merged.get(key) is not None:
            argv.extend([flag, str(int(merged[key]))])
    for key, flag in ITEM_BOOL_FLAGS.items():
        if merged.get(key) is True:
            argv.append(flag)
    argv.extend(["--output-dir", str(item_output_dir)])
    return argv


def item_label(merged: dict[str, Any], index: int) -> str:
    """给每个 item 取一个稳定的目录名/显示名。"""
    raw = as_text(merged.get("id")) or as_text(merged.get("title"))
    if not raw and as_text(merged.get("brief")):
        raw = Path(as_text(merged.get("brief"))).stem
    if not raw and as_text(merged.get("content_file")):
        raw = Path(as_text(merged.get("content_file"))).stem
    return cli.slugify(raw or f"item-{index}")


def collect_publish_inputs(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[Path]]]:
    images: list[dict[str, Any]] = []
    sidecars: dict[str, list[Path]] = {}
    for record in results:
        if record.get("status") != "ok":
            continue
        item_dir = Path(as_text(record.get("output_dir")))
        manifest_path = item_dir / "image-asset-manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = cli.load_json(manifest_path, "单项 image manifest")
        label = as_text(record.get("title")) or as_text(record.get("id")) or item_dir.name
        final_image = Path(as_text(record.get("final_image"))).expanduser()
        if final_image.is_file():
            images.append({"path": final_image, "label": label})
        else:
            for image in asset_publish.image_records_from_manifest(manifest, label):
                image["label"] = label
                images.append(image)
        sidecars[as_text(record.get("id")) or item_dir.name] = [
            item_dir / "image_brief.json",
            *sorted(item_dir.glob("*.prompt.md")),
            item_dir / "image-generation.stdout.log",
            item_dir / "image-generation.stderr.log",
        ]
        overlay_result = Path(as_text(record.get("text_overlay_result"))).expanduser()
        if overlay_result.is_file():
            sidecars[as_text(record.get("id")) or item_dir.name].append(overlay_result)
    return images, sidecars


def _merged_probe_value(defaults: dict[str, Any], items: list[Any], key: str) -> str:
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        merged = merge_item(defaults, raw_item)
        value = as_text(merged.get(key))
        if value:
            return value
    return as_text(defaults.get(key))


def resolve_concurrency(
    args: argparse.Namespace,
    defaults: dict[str, Any],
    items: list[Any],
    output_dir: Path,
) -> dict[str, Any]:
    requested = as_text(getattr(args, "concurrency", "")) or "auto"
    total = max(1, len(items))
    requested_limit: int | None = None
    if requested.lower() != "auto":
        try:
            requested_limit = int(requested)
        except ValueError as exc:
            raise ImageFactoryError("--concurrency 必须是正整数或 auto") from exc
        if requested_limit < 1:
            raise ImageFactoryError("--concurrency 必须大于等于 1")

    raw_limit = requested_limit or 1
    return {
        "requested": requested,
        "resolved": min(raw_limit, total),
        "limit": raw_limit,
        "source": "local_compile_workers",
        "status": "ok",
    }


def run_one_item(
    *,
    args: argparse.Namespace,
    batch_dir: Path,
    defaults: dict[str, Any],
    output_dir: Path,
    index: int,
    total: int,
    raw_item: Any,
) -> dict[str, Any]:
    if not isinstance(raw_item, dict):
        return {
            "index": index,
            "id": f"item-{index}",
            "status": "failed",
            "error": "item 必须是 object",
        }

    merged = merge_item(defaults, raw_item)
    label = item_label(merged, index)
    item_dir = output_dir / f"{index:02d}-{label}"
    record: dict[str, Any] = {
        "index": index,
        "id": label,
        "title": as_text(merged.get("title")) or as_text(merged.get("id")) or label,
        "output_dir": str(item_dir),
    }
    try:
        inline_brief = merged.pop("inline_brief", None)
        selection_receipt = merged.pop("selection_receipt", None)
        if isinstance(inline_brief, dict):
            if isinstance(selection_receipt, dict):
                inline_brief.setdefault("selection_receipt", selection_receipt)
            item_dir.mkdir(parents=True, exist_ok=True)
            brief_path = item_dir / "content-image-brief.json"
            cli.write_json(brief_path, inline_brief)
            merged["brief"] = str(brief_path)
        argv = item_to_argv(merged, batch_dir, item_dir)
        item_args = cli.build_parser().parse_args(argv)
        returncode = cli.run(item_args)
        record["status"] = "ok" if returncode == 0 else "failed"
        record["returncode"] = returncode
        if returncode == 0:
            manifest_path = item_dir / "image-asset-manifest.json"
            item_manifest = cli.load_json(manifest_path, "单项 image manifest")
            record["manifest_path"] = str(manifest_path)
            reference_plan = item_manifest.get("reference_plan")
            if isinstance(reference_plan, dict):
                record["reference_strategy"] = reference_plan.get("strategy")
                record["selection_required"] = bool(reference_plan.get("selection_required"))
            if isinstance(item_manifest.get("render_handoff"), dict):
                record["render_handoff"] = item_manifest["render_handoff"]
            if isinstance(item_manifest.get("render_candidates"), list):
                record["render_candidates"] = item_manifest["render_candidates"]
            if isinstance(item_manifest.get("visual_qa"), dict):
                record["visual_qa"] = item_manifest["visual_qa"]
            if isinstance(item_manifest.get("delivery"), dict):
                record["delivery"] = item_manifest["delivery"]
    except ImageFactoryError as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
    except SystemExit as exc:  # argparse 对非法参数会直接退出,转成单项失败
        record["status"] = "failed"
        record["error"] = f"参数解析失败(exit {exc.code})"
    marker = "✓" if record["status"] == "ok" else "✗"
    print(f"{marker} [{index}/{total}] {label}: {record.get('error', record['status'])}")
    return record


def run_items(
    *,
    args: argparse.Namespace,
    batch_dir: Path,
    defaults: dict[str, Any],
    items: list[Any],
    output_dir: Path,
    concurrency: int,
) -> list[dict[str, Any]]:
    total = len(items)
    if concurrency <= 1 or total <= 1:
        return [
            run_one_item(
                args=args,
                batch_dir=batch_dir,
                defaults=defaults,
                output_dir=output_dir,
                index=index,
                total=total,
                raw_item=raw_item,
            )
            for index, raw_item in enumerate(items, 1)
        ]

    results_by_index: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="image-batch") as pool:
        futures = {
            pool.submit(
                run_one_item,
                args=args,
                batch_dir=batch_dir,
                defaults=defaults,
                output_dir=output_dir,
                index=index,
                total=total,
                raw_item=raw_item,
            ): index
            for index, raw_item in enumerate(items, 1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results_by_index[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - keep one item failure from killing the batch
                results_by_index[index] = {
                    "index": index,
                    "id": f"item-{index}",
                    "status": "failed",
                    "error": str(exc),
                }
    return [results_by_index[index] for index in sorted(results_by_index)]


def _is_ip_video_item(defaults: dict[str, Any], item: Any) -> bool:
    return isinstance(item, dict) and as_text(merge_item(defaults, item).get("scene")) == "ip_video_keyframe"


def _raw_image_from_item_manifest(manifest: dict[str, Any]) -> Path | None:
    generation = manifest.get("image_generation") if isinstance(manifest.get("image_generation"), dict) else {}
    images = generation.get("images") if isinstance(generation.get("images"), list) else []
    for image in images:
        if isinstance(image, dict) and as_text(image.get("path")):
            path = Path(as_text(image.get("path"))).expanduser()
            if path.is_file():
                return path.resolve()
    expected = manifest.get("expected_outputs") if isinstance(manifest.get("expected_outputs"), dict) else {}
    path = Path(as_text(expected.get("raw_image"))).expanduser()
    return path.resolve() if path.is_file() else None


def finalize_ip_video_results(
    *,
    batch_data: dict[str, Any],
    results: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any] | None:
    config = batch_data.get("postprocess") if isinstance(batch_data.get("postprocess"), dict) else {}
    if not config.get("enabled") or as_text(config.get("type")) != "video_text_overlay":
        return None

    overlay_items: list[dict[str, Any]] = []
    for record in results:
        if record.get("status") != "ok":
            continue
        item_dir = Path(as_text(record.get("output_dir")))
        brief_path = item_dir / "image_brief.json"
        asset_manifest_path = item_dir / "image-asset-manifest.json"
        if not brief_path.is_file() or not asset_manifest_path.is_file():
            raise ImageFactoryError(f"IP 视频后处理缺少 brief 或 image manifest: {item_dir}")
        brief = cli.load_json(brief_path, "IP 视频 brief")
        if as_text(brief.get("scene")) != "ip_video_keyframe":
            continue
        asset_manifest = cli.load_json(asset_manifest_path, "IP 视频 image manifest")
        raw_image = _raw_image_from_item_manifest(asset_manifest)
        if raw_image is None:
            raise ImageFactoryError(f"IP 视频后处理找不到 raw 图片: {item_dir}")
        content = brief.get("content") if isinstance(brief.get("content"), dict) else {}
        composition = brief.get("composition") if isinstance(brief.get("composition"), dict) else {}
        layout_variant = as_text(composition.get("text_layout_variant"))
        if layout_variant not in {"square-left", "square-right"}:
            raise ImageFactoryError(
                f"1:1 IP 视频 brief 必须声明 composition.text_layout_variant: {brief_path}"
            )
        overlay_items.append(
            {
                "id": as_text(record.get("id")) or item_dir.name,
                "input_image": str(raw_image),
                "output_file": f"{as_text(record.get('id')) or item_dir.name}-final.png",
                "layout_variant": layout_variant,
                "kicker": as_text(content.get("subheadline")),
                "headline": as_text(content.get("headline")),
                "support": as_text(content.get("summary")),
            }
        )
    if not overlay_items:
        return None

    overlay_manifest = {
        "schema_version": "video-text-overlay/v1",
        "output_dir": str((output_dir / "final").resolve()),
        "items": overlay_items,
    }
    overlay_manifest_path = output_dir / "video-text-overlay.json"
    overlay_manifest_path.write_text(json.dumps(overlay_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    script_path = cli.repo_root_from_script() / "scripts" / "compose_video_keyframe_text.py"
    completed = subprocess.run(
        [sys.executable, str(script_path), "--manifest", str(overlay_manifest_path)],
        cwd=cli.repo_root_from_script(),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ImageFactoryError(f"IP 视频确定性叠字失败: {completed.stderr.strip() or completed.stdout.strip()}")
    result_path = output_dir / "final" / "video-text-overlay-result.json"
    if not result_path.is_file():
        raise ImageFactoryError(f"IP 视频确定性叠字未生成结果 manifest: {result_path}")
    overlay_result = cli.load_json(result_path, "IP 视频文字合成结果")
    by_id = {
        as_text(item.get("id")): item
        for item in overlay_result.get("items", [])
        if isinstance(item, dict)
    }
    for record in results:
        rendered = by_id.get(as_text(record.get("id")))
        if not rendered:
            continue
        final_image = Path(as_text(rendered.get("output_image"))).expanduser()
        if not final_image.is_file():
            raise ImageFactoryError(f"IP 视频最终图不存在: {final_image}")
        record["raw_image"] = as_text(rendered.get("input_image"))
        record["final_image"] = str(final_image.resolve())
        record["text_overlay_result"] = str(result_path.resolve())
    return {
        "type": "video_text_overlay",
        "manifest": str(overlay_manifest_path.resolve()),
        "result_manifest": str(result_path.resolve()),
        "final_count": len(by_id),
    }


def run_batch(args: argparse.Namespace) -> int:
    batch_path = Path(args.batch).expanduser().resolve()
    batch = load_batch(batch_path)
    batch_dir = batch_path.parent
    defaults = batch.get("defaults") if isinstance(batch.get("defaults"), dict) else {}
    items = batch.get("items")
    batch_id = cli.slugify(as_text(batch.get("id")) or batch_path.stem, "image-batch")

    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = cli.repo_root_from_script() / "outputs" / "image-batches" / f"{stamp}-{batch_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    concurrency_decision = resolve_concurrency(args, defaults, items, output_dir)
    concurrency = int(concurrency_decision.get("resolved") or 1)
    print(
        "batch concurrency: "
        f"{concurrency_decision.get('requested')} -> {concurrency} "
        f"({concurrency_decision.get('source')})"
    )
    results = run_items(
        args=args,
        batch_dir=batch_dir,
        defaults=defaults,
        items=items,
        output_dir=output_dir,
        concurrency=concurrency,
    )

    postprocess_result = None

    succeeded = sum(1 for record in results if record["status"] == "ok")
    manifest = {
        "tool": "image-factory-batch",
        "schema_version": BATCH_SCHEMA_VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "compile_only": True,
        "batch_file": str(batch_path),
        "batch_id": batch_id,
        "defaults": defaults,
        "total": len(items),
        "succeeded": succeeded,
        "failed": len(items) - succeeded,
        "output_dir": str(output_dir),
        "concurrency": concurrency_decision,
        "items": results,
    }
    if postprocess_result:
        manifest["postprocess"] = postprocess_result
    manifest_path = output_dir / "image-batch-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"batch manifest: {manifest_path}")
    print(f"成功 {succeeded}/{len(items)},模式: compile-only；handoff: image-render-handoff/v1")
    return 0 if succeeded == len(items) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile a batch of image assets without calling an image API.")
    parser.add_argument("--batch", required=True, help="image-batch/v1 manifest JSON path")
    parser.add_argument("--output-dir", help="Batch output directory; default outputs/image-batches/<timestamp>-<batch-id>")
    parser.add_argument("--concurrency", default="auto", help="Local compile worker count; auto resolves to one")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_batch(args)
    except ImageFactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
