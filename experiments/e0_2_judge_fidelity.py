"""E0-2 ジャッジの一致度と安定性（live Sonnet 5 と決定的代替判定器）。

E4-5 は live が使えなかったため決定的代替判定器で実施した。そこで測れなかった 2 点
  (1) 代替判定器は本物のジャッジと一致するのか
  (2) 本物のジャッジは再判定に対して安定なのか（代替判定器では定義上 1.0 になる）
をここで測る。`IMPROVEMENT.md` の R1。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from _common import corpus, finalize, labeled

from agenteval.core.registry import get_task
from agenteval.llm.client import LLMClient
from agenteval.llm.models import model_id
from agenteval.process import step_judge as step_judge_mod
from agenteval.reports import plotting

N_STEPS = 30
MUTATIONS: list[Any] = ["wrong_tool", "wrong_args", "destructive"]
CACHE = Path("data/results/E0-2_judgements.json")

METHOD = """E4-5 と同じ手順で v01_baseline の合格 run から 30 ステップを抽出し、
3 種類（誤ツール / 誤引数 / 破壊的操作）に変異させた 90 件を加えた計 120 件を作った。
この 120 件を、
  (a) 決定的代替判定器 `judge/rubric.py: offline_step_judge`
  (b) live の `claude-sonnet-5`（`submit_verdict` ツール ＋ `tool_choice` 強制、
      `output_config.effort = low`）
の両方で判定した。さらに (b) では元の 30 ステップを**もう一度**判定し、再判定の安定性を測った。
入力は E4-5 と同じく「直近 3 ステップのイベント ＋ 期待されるツール ＋ 状態が変わったか ＋ 当該行動」で、
自己申告文は含めない（原典 5.2）。
判定結果は `data/results/E0-2_judgements.json` に保存し、再実行時は live を呼ばずに再利用する。"""


def collect(live: bool) -> dict[str, Any]:
    """live ジャッジと代替判定器の判定を集める（保存済みがあれば再利用）。"""
    if CACHE.exists() and not live:
        import json

        return dict(json.loads(CACHE.read_text(encoding="utf-8")))

    runs = corpus()
    passing = sorted(
        (r for r in runs if r.version_id == "v01_baseline" and r.passed() and r.repeat == 0),
        key=lambda r: r.task_id,
    )
    samples: list[step_judge_mod.StepSample] = []
    for run in passing:
        samples.extend(step_judge_mod.extract_steps(get_task(run.task_id), run))
        if len(samples) >= N_STEPS:
            break
    samples = samples[:N_STEPS]

    client = LLMClient(mode="record", run_id="E0-2") if live else None
    payloads = {"original": [s.payload() for s in samples]}
    for kind in MUTATIONS:
        payloads[kind] = [step_judge_mod.mutate(s, kind).payload() for s in samples]

    def judge_all(items: list[str], use_live: bool) -> list[int]:
        from agenteval.judge.rubric import STEP_RUBRIC, offline_step_judge
        from agenteval.judge.verdict import ask

        return [
            ask(client if use_live else None, STEP_RUBRIC, p, offline_step_judge).score
            for p in items
        ]

    data: dict[str, Any] = {"n_steps": len(samples), "offline": {}, "live": {}}
    for key, items in payloads.items():
        data["offline"][key] = judge_all(items, use_live=False)
        data["live"][key] = judge_all(items, use_live=live)
    data["live_rejudge"] = judge_all(payloads["original"], use_live=live)
    data["usd"] = round(sum(e.usd for e in _ledger_since()), 4) if live else 0.0

    import json

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _ledger_since() -> list[Any]:
    from agenteval.llm.cost import read_ledger

    return [e for e in read_ledger() if e.run_id == "E0-2"]


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    data = collect(live)
    offline, live_scores = data["offline"], data["live"]
    n = data["n_steps"]

    pairs = [
        (o, l)
        for key in ["original", *MUTATIONS]
        for o, l in zip(offline[key], live_scores[key], strict=True)
    ]
    within1 = float(np.mean([abs(o - l) <= 1 for o, l in pairs]))
    exact = float(np.mean([o == l for o, l in pairs]))

    drops_live = [
        live_scores[kind][i] < live_scores["original"][i] for kind in MUTATIONS for i in range(n)
    ]
    drops_offline = [
        offline[kind][i] < offline["original"][i] for kind in MUTATIONS for i in range(n)
    ]
    rejudge = float(
        np.mean(
            [
                abs(a - b) <= 1
                for a, b in zip(live_scores["original"], data["live_rejudge"], strict=True)
            ]
        )
    )
    rejudge_exact = float(
        np.mean(
            [a == b for a, b in zip(live_scores["original"], data["live_rejudge"], strict=True)]
        )
    )

    fig = plotting.bar_compare(
        "E0-2",
        "mean_scores",
        "平均スコア（live ジャッジ と 決定的代替判定器）",
        [f"{k}\nlive" for k in ["original", *MUTATIONS]]
        + [f"{k}\noffline" for k in ["original", *MUTATIONS]],
        [float(np.mean(live_scores[k])) for k in ["original", *MUTATIONS]]
        + [float(np.mean(offline[k])) for k in ["original", *MUTATIONS]],
        "スコア (1-5)",
        "live vs simulated",
        threshold=3.5,
    )

    metrics = {
        "score_agreement_within_1": labeled(round(within1, 4), "live"),
        "mutation_drop_rate_live": labeled(round(float(np.mean(drops_live)), 4), "live"),
        "rejudge_agreement_live": labeled(round(rejudge, 4), "live"),
        "original_mean_score_live": labeled(
            round(float(np.mean(live_scores["original"])), 4), "live"
        ),
        "score_agreement_exact": labeled(round(exact, 4), "live"),
        "rejudge_agreement_exact": labeled(round(rejudge_exact, 4), "live"),
        "mutation_drop_rate_offline": labeled(round(float(np.mean(drops_offline)), 4), "simulated"),
        "original_mean_score_offline": labeled(
            round(float(np.mean(offline["original"])), 4), "simulated"
        ),
        "n_judgements": labeled(len(pairs) + n, "live"),
        "judge_model": labeled(model_id("judge"), "live"),
        "live_usd": labeled(data.get("usd", 0.0), "live"),
    }
    notes = [
        f"変異ごとの平均スコア（live）: { {k: round(float(np.mean(live_scores[k])), 2) for k in ['original', *MUTATIONS]} }",
        f"変異ごとの平均スコア（代替判定器）: { {k: round(float(np.mean(offline[k])), 2) for k in ['original', *MUTATIONS]} }",
        (
            "**この実験でしか測れないこと**: E4-5 の `rejudge_agreement` は代替判定器が決定的なので"
            "定義上 1.0 になり、「判定の安定性」を検証したことにならなかった。"
            f"live のジャッジで測ると ±1 以内の一致が {round(rejudge, 3)}、完全一致が {round(rejudge_exact, 3)} である。"
        ),
        (
            "ジャッジのモデル ID は `claude-sonnet-5`、呼び出し数は `n_judgements` に記載。"
            "`.claude/rules/process.md` の「ジャッジのモデルは固定し、結果ページにモデル ID を書く」に従う。"
        ),
    ]
    notes.append(
        "判定は NEGATIVE。仮説の後半（live ジャッジは再判定に対して安定）は強く成立した"
        f"（±1 以内 {round(rejudge, 3)}、完全一致 {round(rejudge_exact, 3)}）が、"
        f"前半（代替判定器は live と十分一致する）は成立しなかった"
        f"（±1 以内 {round(within1, 3)} < 0.7、完全一致 {round(exact, 3)}）。"
    )
    notes.append(
        "不一致の中身ははっきりしている。**代替判定器は一貫して甘い**。"
        f"元のステップで live {round(float(np.mean(live_scores['original'])), 2)} に対し代替 "
        f"{round(float(np.mean(offline['original'])), 2)}、"
        f"誤ツール変異では live {round(float(np.mean(live_scores['wrong_tool'])), 2)} に対し代替 "
        f"{round(float(np.mean(offline['wrong_tool'])), 2)} と 2 点以上開く。"
        "`offline_step_judge` は「期待ツール集合に無い かつ 読み取り系でもない」ときだけ減点する規則なので、"
        "読み取り系ツールへの誤置換をほとんど罰しない。live の Sonnet 5 は"
        "「その状況で意味を成さない行動」として強く減点していた。"
    )
    notes.append(
        "**E4-5 への影響**: E4-5 の基準は live でも満たされる"
        f"（変異の低下率 {round(float(np.mean(drops_live)), 3)} ≥ 0.8、"
        f"元の平均 {round(float(np.mean(live_scores['original'])), 2)} ≥ 3.5、"
        f"再判定一致 {round(rejudge, 3)} ≥ 0.8）。E4-5 の結論（変異ステップは低く評価される）は"
        "live でも支持される。信用できないのは**スコアの絶対水準**であって、変異を検出する向きではない。"
    )
    notes.append(
        "代替判定器を live に合わせて調整すれば一致率は上がるが、それは基準に合うまで実装を弄ることに"
        "なるので行わない（CLAUDE.md）。採るべき道は、ジャッジが要る実験では live を使い、"
        "代替判定器は「向きだけを見る用途」に限ると明記することである。"
    )
    return finalize(
        "E0-2",
        metrics,
        METHOD,
        notes,
        figures=[("平均スコア", fig)],
        seed=seed,
        provenance="live",
        usd=data.get("usd", 0.0),
        failure_type="negative",
    )


if __name__ == "__main__":
    import json

    print(json.dumps(main(live="--live" in sys.argv), ensure_ascii=False, indent=2))
