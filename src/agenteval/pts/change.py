"""版と版の差分（原典 3.1）。

粒度: プロンプトは section マーカー単位、ツールはツール名単位、モデル・設定は「全体」。
"""

from __future__ import annotations

from typing import Literal

from agenteval.core.registry import Version, get_version
from agenteval.core.schema import Change

ChangeKind = Literal["prompt", "model", "tool", "config", "fixture", "code"]
from agenteval.env.tools import tool_schemas


def diff_versions(base: Version | str, target: Version | str) -> list[Change]:
    """2 つの版の差分を種類ごとに返す。差分が無い種類は含めない。"""
    a = get_version(base) if isinstance(base, str) else base
    b = get_version(target) if isinstance(target, str) else target
    changes: list[Change] = []

    sections_a, sections_b = a.section_bodies(), b.section_bodies()
    changed_sections = {
        key
        for key in set(sections_a) | set(sections_b)
        if sections_a.get(key) != sections_b.get(key)
    }
    if changed_sections:
        changes.append(Change(kind="prompt", base=a.id, target=b.id, components=changed_sections))

    if a.toolset != b.toolset:
        schema_a = {s["name"]: s for s in tool_schemas(a.toolset)}
        schema_b = {s["name"]: s for s in tool_schemas(b.toolset)}
        changed_tools = {
            name
            for name in set(schema_a) | set(schema_b)
            if schema_a.get(name) != schema_b.get(name)
        }
        if changed_tools:
            changes.append(Change(kind="tool", base=a.id, target=b.id, components=changed_tools))

    if a.model_id() != b.model_id():
        changes.append(Change(kind="model", base=a.id, target=b.id, components={"model"}))

    config_a, config_b = a.config.model_dump(), b.config.model_dump()
    changed_config = {k for k in config_a if config_a[k] != config_b.get(k)}
    if changed_config:
        changes.append(Change(kind="config", base=a.id, target=b.id, components=changed_config))

    return changes


def merge(changes: list[Change]) -> Change | None:
    """複数の差分を 1 つにまとめる（種類は最も上位のものを採用する）。"""
    if not changes:
        return None
    order = ["config", "prompt", "tool", "fixture", "model", "code"]
    top = max(changes, key=lambda c: order.index(c.kind))
    components: set[str] = set()
    for change in changes:
        components |= change.components
    return Change(kind=top.kind, base=top.base, target=top.target, components=components)


def synthetic(
    kind: str, components: set[str], base: str = "v01_baseline", target: str = "synthetic"
) -> Change:
    """対照用の合成変更。"""
    return Change(kind=kind, base=base, target=target, components=components)
