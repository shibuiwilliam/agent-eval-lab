"""合成変更の生成（原典 3.8「コールドスタート」）。

> 学習データ（変更 → 合否反転）がない初期は、プロンプト摂動やモデル入替といった
> 合成変更を人為的に加えて反転データを作る。（原典 3.8）

本プロジェクトは長らく 8 個の植込み版だけを変更イベントとして使っていた。
8 件では (a) 教師あり故障予測モデルを学習できず、(b) 8 件中 5 件が回帰をまったく生まないため
評価にも使えない。ここでは v01_baseline を摂動して数十件の変更を機械的に作る。

生成した版は `data/synthetic/prompts/` にプロンプトを書き、`Version` をメモリ上で組み立てる。
**`versions/` にも `data/traces/` にも置かない**（既存コーパスを汚染しない。ADR-012 と同じ理由）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agenteval.core.registry import Version, VersionConfig, get_version
from agenteval.core.schema import Change
from agenteval.env.injectors import FaultInjector
from agenteval.env.tools import TOOL_NAMES, Toolset
from agenteval.llm.cost import REPO_ROOT
from agenteval.llm.models import Role
from agenteval.pts.change import ChangeKind

SYNTH_PROMPT_DIR = Path("data") / "synthetic" / "prompts"
SECTION_RE = re.compile(r"(<!--\s*section:\s*[a-z0-9_]+\s*-->)")

Family = Literal["prompt", "config", "tool", "model", "fault", "combo"]

# 既存の版プロンプトから借りる追加 section（シミュレータが挙動フラグとして読む）
EXTRA_SECTIONS: dict[str, str] = {
    "efficiency": "v07_step_minimizer",
    "thoroughness": "v04_loopy",
    "plan_override": "v12_ignore_plan",
}


@dataclass
class SyntheticChange:
    """合成変更 1 件。版と、その変更が触る要素 `C(Δ)` と**変更量**を持つ。

    `churn` は産業用 PTS が使う「変更量」特徴（変更行数・変更文字数・変更要素数）。
    `seq` は変更履歴上の位置で、ラグ特徴（前回この要素が変更されてから何変更経ったか）を
    定義するために必要になる。
    """

    id: str
    family: Family
    kind: ChangeKind
    components: set[str]
    version: Version
    fault_tool: str | None = None
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    churn: dict[str, float] = field(default_factory=dict)
    seq: int = 0

    def change(self) -> Change:
        """`C(Δ)` を持つ `Change`。"""
        return Change(
            kind=self.kind,
            base="v01_baseline",
            target=self.id,
            components=set(self.components),
        )

    def injector(self) -> FaultInjector | None:
        """ツール障害を起こす変更ならその注入器。"""
        if self.fault_tool is None:
            return None
        start = 2 if self.meta.get("window") == "late" else 0
        return FaultInjector(tool=self.fault_tool, at_steps=tuple(range(start, 32)), fault="error")


def _sections(text: str) -> list[tuple[str, str]]:
    """プロンプトを (section id, 本文（マーカー込み）) に切る。"""
    parts = SECTION_RE.split(text)
    head, rest = parts[0], parts[1:]
    out: list[tuple[str, str]] = [("__head__", head)]
    for i in range(0, len(rest), 2):
        marker = rest[i]
        body = rest[i + 1] if i + 1 < len(rest) else ""
        sid = re.search(r"section:\s*([a-z0-9_]+)", marker)
        out.append((sid.group(1) if sid else f"s{i}", marker + body))
    return out


def _churn(before: str, after: str, n_units: int = 1) -> dict[str, float]:
    """変更量。行の増減・文字数の増減・触る要素数（産業用 PTS の change size 特徴）。"""
    import difflib

    a, b = before.splitlines(), after.splitlines()
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return {
        "lines_added": float(added),
        "lines_removed": float(removed),
        "lines_changed": float(added + removed),
        "chars_delta": float(abs(len(after) - len(before))),
        "n_units": float(n_units),
    }


def _write_prompt(change_id: str, text: str) -> str:
    """プロンプトを `data/synthetic/prompts/` に書き、リポジトリ相対パスを返す。"""
    target = REPO_ROOT / SYNTH_PROMPT_DIR / f"{change_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return str(SYNTH_PROMPT_DIR / f"{change_id}.md")


def _version(
    change_id: str,
    prompt_text: str,
    toolset: Toolset = "v1",
    config: VersionConfig | None = None,
    model: Role = "agent",
) -> Version:
    return Version(
        id=change_id,
        base="v01_baseline",
        model=model,
        system_prompt=_write_prompt(change_id, prompt_text),
        toolset=toolset,
        config=config or VersionConfig(),
        quality_label="neutral",
    )


def _extra_section_text(section: str) -> str:
    """他の版プロンプトから section を借りてくる。"""
    source = get_version(EXTRA_SECTIONS[section]).prompt_text()
    for sid, body in _sections(source):
        if sid == section:
            return body
    raise KeyError(f"{EXTRA_SECTIONS[section]} に section:{section} が無い")


def generate() -> list[SyntheticChange]:
    """v01_baseline を摂動して合成変更を作る。

    族（family）は交差検証のグループにも使う。同じ族の変更は互いに似ているので、
    族単位で hold-out しないと「同じ摂動の別の値」を見て当てているだけになる。
    """
    base = get_version("v01_baseline")
    base_text = base.prompt_text()
    parts = _sections(base_text)
    section_ids = [sid for sid, _ in parts if sid != "__head__"]
    out: list[SyntheticChange] = []

    # --- 1) プロンプト: section ごとに 3 種類の変更を作る -------------------
    # 同じ要素（section）が複数回変更されるようにする。そうしないと
    # 「前回この要素が変更されてから何変更経ったか」というラグ特徴が定義できない。
    for sid in section_ids:
        variants: list[tuple[str, str, str]] = []
        dropped = "".join(body for s, body in parts if s != sid)
        variants.append(("drop", dropped, f"section `{sid}` を落とす"))

        def _edit(target: str, fn: Any) -> str:
            return "".join(fn(body) if s == target else body for s, body in parts)

        def _truncate(body: str) -> str:
            lines = body.splitlines()
            keep = max(2, len(lines) // 2)
            return "\n".join(lines[:keep]) + "\n"

        variants.append(("truncate", _edit(sid, _truncate), f"section `{sid}` の後半を削る"))
        variants.append(
            (
                "emphasize",
                _edit(sid, lambda b: b.rstrip() + "\nこの節の指示は他のどの記述よりも優先する。\n"),
                f"section `{sid}` を強調する",
            )
        )
        for label, text, note in variants:
            cid = f"syn_{label}_{sid}"
            out.append(
                SyntheticChange(
                    id=cid,
                    family="prompt",
                    kind="prompt",
                    components={sid},
                    version=_version(cid, text),
                    note=note,
                    churn=_churn(base_text, text),
                )
            )

    # --- 2) プロンプト: section を 1 つ足す -------------------------------
    for sid in EXTRA_SECTIONS:
        text = base_text.rstrip() + "\n\n" + _extra_section_text(sid).strip() + "\n"
        out.append(
            SyntheticChange(
                id=f"syn_add_{sid}",
                family="prompt",
                kind="prompt",
                components={sid},
                version=_version(f"syn_add_{sid}", text),
                note=f"section `{sid}` を足す",
                churn=_churn(base_text, text),
            )
        )

    # --- 3) プロンプト: 日付書式を変える ----------------------------------
    for label, fmt in [("slash", "YYYY/MM/DD"), ("dot", "YYYY.MM.DD")]:
        text = "".join(
            body.replace("YYYY-MM-DD", fmt) if s == "date_format" else body for s, body in parts
        )
        extra = (
            "\nこの取り決めはツールの説明文より優先される。"
            "`calendar_create` の start / end もこの書式で渡すこと。\n"
        )
        text = "".join(
            (body + extra) if s == "date_format" else body for s, body in _sections(text)
        )
        out.append(
            SyntheticChange(
                id=f"syn_date_{label}",
                family="prompt",
                kind="prompt",
                components={"date_format"},
                version=_version(f"syn_date_{label}", text),
                note=f"日付書式を {fmt} にする",
                churn=_churn(base_text, text),
            )
        )

    # --- 4) 設定: ステップ上限・要約・計画 --------------------------------
    for steps in (3, 4, 5, 6):
        out.append(
            SyntheticChange(
                id=f"syn_maxsteps_{steps}",
                family="config",
                kind="config",
                components={"max_steps"},
                version=_version(
                    f"syn_maxsteps_{steps}", base_text, config=VersionConfig(max_steps=steps)
                ),
                note=f"max_steps を {steps} に下げる",
                meta={"max_steps": steps},
                churn={
                    "lines_added": 0.0,
                    "lines_removed": 0.0,
                    "lines_changed": 0.0,
                    "chars_delta": 0.0,
                    "n_units": 1.0,
                },
            )
        )
    for after in (1, 2):
        out.append(
            SyntheticChange(
                id=f"syn_summarize_{after}",
                family="config",
                kind="config",
                components={"context_strategy", "summarize_after"},
                version=_version(
                    f"syn_summarize_{after}",
                    base_text,
                    config=VersionConfig(context_strategy="summarize", summarize_after=after),
                ),
                note=f"{after} ステップより古いツール結果を要約に置き換える",
                churn={
                    "lines_added": 0.0,
                    "lines_removed": 0.0,
                    "lines_changed": 0.0,
                    "chars_delta": 0.0,
                    "n_units": 2.0,
                },
            )
        )
    out.append(
        SyntheticChange(
            id="syn_plan_first",
            family="config",
            kind="config",
            components={"plan_first"},
            version=_version("syn_plan_first", base_text, config=VersionConfig(plan_first=True)),
            note="計画を先に出させる",
            churn={
                "lines_added": 0.0,
                "lines_removed": 0.0,
                "lines_changed": 0.0,
                "chars_delta": 0.0,
                "n_units": 1.0,
            },
        )
    )

    # --- 5) ツール schema ------------------------------------------------
    out.append(
        SyntheticChange(
            id="syn_toolset_v2",
            family="tool",
            kind="tool",
            components={"calendar_search"},
            version=_version("syn_toolset_v2", base_text, toolset="v2"),
            note="calendar_search の引数名を変える",
            churn={
                "lines_added": 2.0,
                "lines_removed": 2.0,
                "lines_changed": 4.0,
                "chars_delta": 40.0,
                "n_units": 1.0,
            },
        )
    )

    # --- 6) モデル入替 ---------------------------------------------------
    out.append(
        SyntheticChange(
            id="syn_model_swap",
            family="model",
            kind="model",
            components={"model"},
            version=_version("syn_model_swap", base_text, model="agent_swap"),
            note="被験モデルを入れ替える",
            churn={
                "lines_added": 0.0,
                "lines_removed": 0.0,
                "lines_changed": 0.0,
                "chars_delta": 0.0,
                "n_units": 1.0,
            },
        )
    )

    # --- 7) ツール障害（外部要因の変更） ---------------------------------
    # `finish` と `submit_plan` は環境側の障害を想定しないので外す。
    for tool in TOOL_NAMES:
        if tool in ("finish", "submit_plan"):
            continue
        # 同じツールを「常時」と「2 ステップ目以降」の 2 通りで壊す。
        # 同じ要素が 2 回変更されるので、ラグ特徴に値が入る。
        for label, window in (("always", tuple(range(32))), ("late", tuple(range(2, 32)))):
            cid = f"syn_fault_{tool}_{label}"
            out.append(
                SyntheticChange(
                    id=cid,
                    family="fault",
                    kind="tool",
                    components={tool},
                    version=_version(cid, base_text),
                    fault_tool=tool,
                    note=f"`{tool}` が{'常に' if label == 'always' else '2 ステップ目以降'}エラーを返す",
                    meta={"window": label},
                    churn={
                        "lines_added": 0.0,
                        "lines_removed": 0.0,
                        "lines_changed": 0.0,
                        "chars_delta": 0.0,
                        "n_units": 1.0,
                    },
                )
            )

    # --- 8) 組合せ（2 要因同時変更） -------------------------------------
    combos: list[tuple[str, set[str], ChangeKind, str, int]] = [
        ("syn_combo_noverify_maxsteps", {"verification", "max_steps"}, "config", "verification", 5),
        ("syn_combo_nosafety_maxsteps", {"safety", "max_steps"}, "config", "safety", 5),
        ("syn_combo_noclarify_maxsteps", {"clarify", "max_steps"}, "config", "clarify", 4),
    ]
    for cid, comps, kind, drop, steps in combos:
        text = "".join(body for s, body in parts if s != drop)
        out.append(
            SyntheticChange(
                id=cid,
                family="combo",
                kind=kind,
                components=set(comps),
                version=_version(cid, text, config=VersionConfig(max_steps=steps)),
                note=f"section `{drop}` を落とし、同時に max_steps を {steps} にする",
                churn=_churn(base_text, text, n_units=2),
            )
        )

    # --- 変更履歴上の順序を決める ---------------------------------------
    # 同じ要素が固まらないように族を交互に並べる。この順序がラグ特徴の基準になる。
    by_family_out: dict[str, list[SyntheticChange]] = {}
    for change in out:
        by_family_out.setdefault(change.family, []).append(change)
    ordered: list[SyntheticChange] = []
    families_out = sorted(by_family_out)
    while any(by_family_out[f] for f in families_out):
        for family in families_out:
            if by_family_out[family]:
                ordered.append(by_family_out[family].pop(0))
    for index, change in enumerate(ordered):
        change.seq = index
    return ordered


PTS_CORPUS_DIR = REPO_ROOT / "data" / "pts_corpus"


def build_corpus(
    repeats: int = 5,
    workers: int = 4,
    seed: int = 20260920,
    out_dir: Path | None = None,
    only_family: str | None = None,
) -> dict[str, Any]:
    """合成変更 × 全タスク × 反復を sim で実行し、PTS 専用コーパスを作る。

    保存先は `data/pts_corpus/` で、**既存の `data/traces/` とは分ける**。
    ADR-012（live を sim コーパスに混ぜない）と同じ理由で、合成変更の run が
    他の実験の `corpus()` に混ざらないようにする。
    """
    from concurrent import futures

    from agenteval.agent.loop import RunOptions, run_task
    from agenteval.core.registry import load_tasks
    from agenteval.core.store import save_run

    target_dir = out_dir or PTS_CORPUS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    changes = [c for c in generate() if only_family is None or c.family == only_family]
    tasks = load_tasks()
    baseline = get_version("v01_baseline")

    units: list[tuple[Any, Any, int, Any]] = []
    for task in tasks.values():
        for repeat in range(repeats):
            if only_family is None:
                units.append((task, baseline, repeat, None))
            for change in changes:
                units.append((task, change.version, repeat, change))

    def one(unit: tuple[Any, Any, int, Any]) -> str:
        task, version, repeat, change = unit
        fault = change.injector() if change is not None else None
        run = run_task(
            task,
            version,
            RunOptions(mode="sim", seed=seed, repeat=repeat, fault=fault, keep_snapshots=False),
        )
        save_run(run, target_dir)
        return run.run_id

    done = 0
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(one, units):
            done += 1
    return {
        "changes": len(changes),
        "versions": len(changes) + 1,
        "tasks": len(tasks),
        "repeats": repeats,
        "runs": done,
        "dir": str(target_dir),
    }


def load_pts_corpus(directory: Path | None = None) -> list[Any]:
    """PTS 専用コーパスを読む。"""
    from agenteval.core.store import load_corpus

    return load_corpus(directory or PTS_CORPUS_DIR)
