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
    """合成変更 1 件。版と、その変更が触る要素 `C(Δ)` を持つ。"""

    id: str
    family: Family
    kind: ChangeKind
    components: set[str]
    version: Version
    fault_tool: str | None = None
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

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
        return FaultInjector(tool=self.fault_tool, at_steps=tuple(range(32)), fault="error")


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

    # --- 1) プロンプト: section を 1 つ落とす -----------------------------
    for sid in section_ids:
        text = "".join(body for s, body in parts if s != sid)
        out.append(
            SyntheticChange(
                id=f"syn_drop_{sid}",
                family="prompt",
                kind="prompt",
                components={sid},
                version=_version(f"syn_drop_{sid}", text),
                note=f"section `{sid}` を落とす",
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
        )
    )

    # --- 7) ツール障害（外部要因の変更） ---------------------------------
    # `finish` と `submit_plan` は環境側の障害を想定しないので外す。
    for tool in TOOL_NAMES:
        if tool in ("finish", "submit_plan"):
            continue
        out.append(
            SyntheticChange(
                id=f"syn_fault_{tool}",
                family="fault",
                kind="tool",
                components={tool},
                version=_version(f"syn_fault_{tool}", base_text),
                fault_tool=tool,
                note=f"`{tool}` が常にエラーを返すようになる",
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
            )
        )

    return out


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

    units: list[tuple[Any, Any, int, str | None]] = []
    for task in tasks.values():
        for repeat in range(repeats):
            if only_family is None:
                units.append((task, baseline, repeat, None))
            for change in changes:
                units.append((task, change.version, repeat, change.fault_tool))

    def one(unit: tuple[Any, Any, int, str | None]) -> str:
        task, version, repeat, fault_tool = unit
        fault = (
            FaultInjector(tool=fault_tool, at_steps=tuple(range(32)), fault="error")
            if fault_tool
            else None
        )
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
