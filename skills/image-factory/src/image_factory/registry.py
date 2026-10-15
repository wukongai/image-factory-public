"""统一模板注册中心 + 业务路由数据加载。

模板侧:让通用模板(templates/)和各垂直 skill 的模板包(skills/*/templates/)共享
同一套发现与解析逻辑。新增模板包只需要新建目录放 JSON 文件,不需要改任何核心代码。
查找优先级:根目录 templates/ 在前,skills/ 按目录名排序在后;同名模板 id 以先找到的为准。

业务路由侧:scripts/image_route_map.json 是业务路由数据源——业务类型 + 模板归属 +
横向正交能力。加新业务 = 改这个 json,不改 SKILL.md。json 缺失时回退到
_route_fallback.ROUTE_MAP_FALLBACK,保证主 SKILL.md 不失效。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from image_factory._route_fallback import ROUTE_MAP_FALLBACK
from image_factory.errors import ImageFactoryError


def template_search_dirs(repo_root: Path) -> list[Path]:
    """返回所有模板目录,根目录 templates/ 优先,其后是每个 skill 的 templates/。"""
    dirs: list[Path] = []
    root_templates = repo_root / "templates"
    if root_templates.is_dir():
        dirs.append(root_templates)
    skills_dir = repo_root / "skills"
    if skills_dir.is_dir():
        for skill in sorted(skills_dir.iterdir()):
            candidate = skill / "templates"
            if candidate.is_dir():
                dirs.append(candidate)
    return dirs


def list_templates(repo_root: Path) -> list[dict[str, Any]]:
    """列出全部可用模板。同名 id 只保留优先级更高目录里的那一个。"""
    entries: dict[str, dict[str, Any]] = {}
    for directory in template_search_dirs(repo_root):
        for path in sorted(directory.glob("*.json")):
            template_id = path.stem
            if template_id in entries:
                continue
            entry: dict[str, Any] = {"id": template_id, "path": path}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    entry["name"] = str(data.get("name") or "").strip()
                    entry["scene"] = str(data.get("scene") or "").strip()
                    entry["size"] = str(data.get("size") or "").strip()
                    entry["default_for_scene"] = bool(data.get("default_for_scene"))
                    # 业务别名:让调用方用「暖纸手绘金句引流卡」这类业务叫法选模板,
                    # 而不必记住工程 id;别名只读,不影响 id 唯一性。
                    raw_aliases = data.get("aliases")
                    if isinstance(raw_aliases, list):
                        entry["aliases"] = [
                            str(a).strip() for a in raw_aliases if str(a).strip()
                        ]
                    raw_legacy_aliases = data.get("legacy_aliases")
                    if isinstance(raw_legacy_aliases, list):
                        entry["legacy_aliases"] = [
                            str(a).strip() for a in raw_legacy_aliases if str(a).strip()
                        ]
            except (OSError, json.JSONDecodeError):
                # 坏文件不应让列表功能整体失败,标记后继续
                entry["error"] = "无法解析的模板 JSON"
            entries[template_id] = entry
    return list(entries.values())


def scene_default_templates(repo_root: Path) -> dict[str, str]:
    """扫描全部模板,返回 场景 → 默认模板 id 映射。

    模板 JSON 里声明 `"scene": "marketing_poster"` + `"default_for_scene": true` 即自动注册,
    新增场景/类型不需要改任何核心代码。同一场景有多个声明时,
    按 template_search_dirs 优先级取第一个(根目录 templates/ 优先)。
    """
    defaults: dict[str, str] = {}
    for entry in list_templates(repo_root):
        scene = str(entry.get("scene") or "").strip()
        if scene and entry.get("default_for_scene") and scene not in defaults:
            defaults[scene] = str(entry["id"])
    return defaults


def resolve_template_path(repo_root: Path, template_value: str) -> Path:
    """把模板 id 或文件路径解析成模板 JSON 的绝对路径。

    - 传入存在的路径(含 ~ 展开)直接使用
    - 传入裸 id 时依次在 template_search_dirs 里找 <id>.json
    - 找不到时报错并列出所有搜索目录,方便排查
    """
    candidate = Path(template_value).expanduser()
    if candidate.exists():
        return candidate.resolve()
    if candidate.suffix:
        raise ImageFactoryError(f"模板不存在: {candidate}")

    search_dirs = template_search_dirs(repo_root)
    for directory in search_dirs:
        bundled = directory / f"{template_value}.json"
        if bundled.exists():
            return bundled.resolve()

    # 别名解析:id/文件名都没命中时,再按模板里声明的业务别名(aliases)匹配,
    # 让调用方用「暖纸手绘金句引流卡」这类叫法选模板。id 优先于别名,避免歧义;
    # 别名命中多个模板时报错并列出,逼调用方改用精确 id。
    needle = template_value.strip()
    lowered_needle = needle.lower()
    alias_hits = [
        entry
        for entry in list_templates(repo_root)
        if any(
            needle == alias or lowered_needle == str(alias).lower()
            for alias in [*entry.get("aliases", []), *entry.get("legacy_aliases", [])]
        )
    ]
    if len(alias_hits) == 1:
        return Path(alias_hits[0]["path"]).resolve()
    if len(alias_hits) > 1:
        ids = "、".join(str(hit["id"]) for hit in alias_hits)
        raise ImageFactoryError(
            f"别名 {template_value!r} 命中多个模板: {ids};请改用精确模板 id"
        )

    searched = "、".join(str(directory) for directory in search_dirs) or "(无模板目录)"
    raise ImageFactoryError(f"未知模板 {template_value!r},已搜索: {searched}")


# ----------------------------------------------------------------------------
# 业务路由(image_route_map.json)
# ----------------------------------------------------------------------------


def load_route_map(repo_root: Path) -> dict[str, Any]:
    """加载 scripts/image_route_map.json;缺失或损坏时回退到硬编码 fallback。

    设计原则:json 是真实数据源(可被改/扩),fallback 只保证最小可用——
    业务路由是主 SKILL.md 的入口契约,绝不能因 json 缺失而失效。
    """
    route_map_path = repo_root / "scripts" / "image_route_map.json"
    if not route_map_path.exists():
        return ROUTE_MAP_FALLBACK
    try:
        data = json.loads(route_map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 坏 json 不应让主 skill 失效;回退到 fallback 并继续
        return ROUTE_MAP_FALLBACK
    if not isinstance(data, dict) or "businesses" not in data:
        return ROUTE_MAP_FALLBACK
    return data


def get_business_by_id(route_map: dict[str, Any], business_id: str) -> dict[str, Any] | None:
    """按 business_id 精确查找;找不到返回 None。"""
    for business in route_map.get("businesses", []):
        if business.get("business_id") == business_id:
            return business
    return None


def get_business_by_alias(route_map: dict[str, Any], alias: str) -> dict[str, Any] | None:
    """按 trigger_aliases / display_name 模糊查找(忽略大小写)。

    返回第一个命中的 business;命中多个时也只返回第一个,业务路由要求 alias 不冲突。
    """
    needle = alias.strip().lower()
    if not needle:
        return None
    for business in route_map.get("businesses", []):
        if business.get("display_name", "").strip().lower() == needle:
            return business
        for trig in business.get("trigger_aliases", []):
            if str(trig).strip().lower() == needle:
                return business
    return None


def get_default_template(
    route_map: dict[str, Any], business_id: str
) -> str | None:
    """返回某业务的默认模板 id。

    优先取 default=true 且 status 为 production / experimental;若 default 标记的是
    deprecated 模板,跳过(防过渡期 deprecated 模板还挂着 default 标记)。找不到返回 None。
    """
    business = get_business_by_id(route_map, business_id)
    if not business:
        return None
    for tpl in business.get("templates", []):
        if tpl.get("default") and tpl.get("status") in ("production", "experimental"):
            return str(tpl.get("id"))
    # 兜底:如果没有 production default,返回第一个 production 模板
    for tpl in business.get("templates", []):
        if tpl.get("status") == "production":
            return str(tpl.get("id"))
    return None
