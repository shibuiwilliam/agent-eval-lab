"""章 C（コーディング環境）の実験スクリプトが共有する処理。

`_common.py`（既存の `finalize`・`labeled`）を土台に、次を足す。
- 結果ページの「方法」を、設計者が書いた結果ページ（docs/results/EC-*.md）から引き継ぐ。
  `finalize` は結果ページ全体を書き直すので、何もしないと設計者の「方法」が短い文字列で潰れる。
- live の実験（EC-B2・R1・R2）が段 `corpus` の run だけを読むための入口（run が無ければ案内付きで止まる）。
- 結果 JSON の来歴ラベル付けと、判定の種類（FAIL / NEGATIVE）の選択。
"""

from __future__ import annotations

import math
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
from _common import finalize, labeled, meta_of
from numpy.typing import NDArray

from agenteval.core.coding_store import trace_path
from agenteval.core.schema import Run
from agenteval.econ.roi import ThetaSample, VersionEconomics
from agenteval.reports import pages

__all__ = [
    "breakeven_summary",
    "carry_over_designer_page",
    "conditional_rob",
    "design_method",
    "designer_verdict_text",
    "failing_metrics",
    "failure_caution",
    "failure_override",
    "finalize_coding",
    "json_safe",
    "labeled",
    "load_existing_run",
    "nv_array",
]

# 見出しは前方一致（「## 方法（改訂、ADR-042。…）」のような付記付きの見出しも拾う）
_METHOD_RE = re.compile(r"^## 方法[^\n]*\n(.*?)\n## 結果", re.DOTALL | re.MULTILINE)


def design_method(experiment_id: str) -> str:
    """設計者が書いた結果ページの「方法」。無ければ空文字列（呼び出し側が代わりを渡す）。

    `finalize` が書く結果ページは `## 方法\\n{method}\\n\\n## 結果` の形なので、再生成しても同じ文字列に戻る
    （冪等）。
    """
    path = pages.RESULTS_DIR / f"{experiment_id}.md"
    if not path.exists():
        return ""
    match = _METHOD_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else ""


def json_safe(value: Any) -> Any:  # Any: JSON にする任意の値
    """nan・inf を文字列にする（JSON に nan を書かない。区別が要る値は呼び出し側が理由を添える）。"""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [json_safe(v) for v in value]
    return value


def failing_metrics(experiment_id: str, metrics: Mapping[str, Any]) -> list[str]:
    """registry の criteria のうち満たしていない指標名。値が無い（None）ものも含む。

    `applies_if` が偽の基準（N/A）は含めない。適用条件を評価できない基準（条件の指標が無い・来歴が無い）は含める。
    適用される基準が 1 つも無いときは、その旨の見出し 1 件を返す（空虚な合格を作らない。ADR-045）。
    """
    out: list[str] = []
    criteria = meta_of(experiment_id).get("criteria", [])
    statuses = []
    for criterion in criteria:
        status = pages.evaluate_criterion(criterion, dict(metrics))["status"]
        statuses.append(status)
        if status in ("fail", "error"):
            out.append(str(criterion["metric"]))
    if criteria and all(s == "na" for s in statuses):
        out.append("（適用される基準が無い）")
    return out


def unevaluable_criteria(experiment_id: str, metrics: Mapping[str, Any]) -> bool:
    """適用条件を評価できない基準がある、または適用される基準が 1 つも無い（判定は failure_type に依らず FAIL）。"""
    statuses = [
        pages.evaluate_criterion(c, dict(metrics))["status"]
        for c in meta_of(experiment_id).get("criteria", [])
    ]
    return "error" in statuses or (bool(statuses) and all(s == "na" for s in statuses))


AUTO_NOTES_MARK = "<!-- agenteval:auto-notes -->"
AUTO_VERDICT_MARK = "<!-- agenteval:auto-verdict -->"
AUTO_VERDICT_HEADING = "### 自動判定"
_UNFILLED = ("", "（未実施）")
_FOOTER = "\n---\n生成:"
_VERDICT_WORDS = ("PASS", "FAIL", "NEGATIVE", "PENDING")
# `experiments/_common.verdict_from` が書く判定理由の書き出し。自動生成の段落の見分けに使う
_AUTO_REASON_PREFIXES = ("全基準を満たした。", "未達の基準:")


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """`## heading…` の見出しの行の先頭から、次の `\n## `・フッター・末尾の手前までの範囲。

    見出しは前方一致（`## 5項目（改訂、…）` のような付記付きの見出しも同じ節として扱う）。
    """
    match = re.search(rf"^## {re.escape(heading)}[^\n]*\n", text, re.MULTILINE)
    if match is None:
        return None
    start = match.start()
    ends = [e for e in (text.find("\n## ", start + 1), text.find(_FOOTER, start)) if e >= 0]
    return start, (min(ends) if ends else len(text))


def _heading_line(text: str, span: tuple[int, int]) -> str:
    """節の見出しの行（付記を含む。改行なし）。"""
    return text[span[0] : span[1]].split("\n", 1)[0]


def _section_body(text: str, span: tuple[int, int]) -> str:
    """節の本文（見出しの行を除く。前後の空行を落とす）。"""
    parts = text[span[0] : span[1]].split("\n", 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _is_auto_paragraph(paragraph: str) -> bool:
    """スクリプトが書く判定の段落か（判定語だけの行、判定理由の定型の書き出し、未記入の印）。"""
    text = paragraph.strip()
    return (
        text in _UNFILLED
        or text in _VERDICT_WORDS
        or text.startswith(_AUTO_REASON_PREFIXES)
        or AUTO_VERDICT_MARK in text
    )


def designer_verdict_text(old_text: str) -> str:
    """既存ページの「## 判定」にある設計者の記述。無ければ空文字列。

    - 「### 自動判定」の小見出しがあれば（このスクリプトが書いた形）、その前が設計者の記述
    - 無ければ（設計者が書いた、または旧形式で生成されたページ）、本文のうち自動生成の段落
      （判定語だけの行・「全基準を満たした。」「未達の基準:」の書き出し・未記入）しか無ければ空、
      そうでなければ本文をそのまま設計者の記述とする（段落を選り分けて壊さない）
    """
    span = _section_span(old_text, "判定")
    if span is None:
        return ""
    body = _section_body(old_text, span)
    auto = re.search(rf"^{re.escape(AUTO_VERDICT_HEADING)}[^\n]*$", body, re.MULTILINE)
    if auto is not None:
        return body[: auto.start()].strip()
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    return "" if all(_is_auto_paragraph(p) for p in paragraphs) else body


def carry_over_designer_page(
    experiment_id: str, old_text: str | None, notes: Sequence[str]
) -> None:
    """`finalize` が書いた結果ページに、設計者が書いた既存ページの記述を引き継ぐ。

    見出しは前方一致で照合する（`## 5項目（改訂、…）` のような付記付きの見出しを取りこぼさない）。
    - 先頭ブロック（タイトル行と「## 5項目」の間の状態・原典の段落）と「## 5項目」を既存ページのものに戻す
    - 「## 方法」を既存ページのものに戻す（本文が空でなければ。見出しの付記も残る）
    - 「## 判定」は、既存ページに設計者の記述（自動生成の印が無い段落）があれば保持し、スクリプトの判定は
      「### 自動判定」の小見出しの下（自動判定の印付き）に出す。再実行で重複しない
    - 既存ページの「## 気づき・限界」が未記入（空・「（未実施）」・自動注記だけ）なら、スクリプトの notes をそこに出す
      （自動注記の印付き。再実行で重複しない）。記入済みなら上書きせず、notes は別の見出し「## 自動注記」に出す
    既存ページが無い・形が違うときは何もしない。
    """
    if not old_text:
        return
    path = pages.RESULTS_DIR / f"{experiment_id}.md"
    new = path.read_text(encoding="utf-8")
    bullets = "\n".join(f"- {n}" for n in notes)

    old_five, new_five = _section_span(old_text, "5項目"), _section_span(new, "5項目")
    if old_five and new_five:
        old_title_end = old_text.find("\n")
        new_title_end = new.find("\n")
        head = old_text[old_title_end + 1 : old_five[0]].strip("\n")
        block = old_text[old_five[0] : old_five[1]].rstrip("\n")
        rebuilt_head = f"\n\n{head}\n\n" if head else "\n\n"
        new = new[:new_title_end] + rebuilt_head + block + "\n" + new[new_five[1] :]

    old_method, new_method = _section_span(old_text, "方法"), _section_span(new, "方法")
    if old_method and new_method and _section_body(old_text, old_method):
        block = old_text[old_method[0] : old_method[1]].rstrip("\n")
        new = new[: new_method[0]] + block + "\n" + new[new_method[1] :]

    new_judge = _section_span(new, "判定")
    if new_judge:
        auto_body = _section_body(new, new_judge)
        designer = designer_verdict_text(old_text)
        lead = f"{designer}\n\n" if designer else ""
        section = f"## 判定\n{lead}{AUTO_VERDICT_HEADING}\n{AUTO_VERDICT_MARK}\n{auto_body}\n"
        new = new[: new_judge[0]] + section + new[new_judge[1] :]

    old_notes, new_notes = (
        _section_span(old_text, "気づき・限界"),
        _section_span(new, "気づき・限界"),
    )
    if new_notes is None:
        path.write_text(new, encoding="utf-8")
        return
    # 既存ページの「気づき・限界」の本文（見出し行と、直後の「## 自動注記」・フッターを除く）
    filled = ""
    heading = "## 気づき・限界"
    if old_notes:
        heading = _heading_line(old_text, old_notes)  # 付記付きの見出しを残す
        body = _section_body(old_text, old_notes)
        auto = body.find("\n## 自動注記")
        filled = (body if auto < 0 else body[:auto]).strip()
    if filled in _UNFILLED or AUTO_NOTES_MARK in filled:
        section = f"{heading}\n{AUTO_NOTES_MARK}\n{bullets}\n"
        new = new[: new_notes[0]] + section + new[new_notes[1] :]
    else:
        section = f"{heading}\n{filled}\n\n## 自動注記\n{bullets}\n"
        new = new[: new_notes[0]] + section + new[new_notes[1] :]
    path.write_text(new, encoding="utf-8")


def load_existing_run(run_id: str, *, data_root: Path, mode_dir: str = "sim") -> Run | None:
    """同じ run_id の run が既にあれば読む（無ければ None）。読めない・壊れていれば案内付きで止まる。

    スクリプト化 run（EC-S1・EC-O2・EC-B2 の対照 (a)）は再実行しても同じ結果になるので、再利用する。
    壊れた記録を黙って消さない: やり直すには data/coding/snapshots/ などの削除が要り、ユーザーの承認が要る（CLAUDE.md）。
    """
    path = trace_path(run_id, mode_dir, data_root=data_root)  # type: ignore[arg-type]
    if not path.exists():
        return None
    try:
        return Run.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"{path} が読めない・壊れている（{type(exc).__name__}）。やり直すには data/coding/snapshots/ と traces/ の"
            "該当 run の削除が要るので、ユーザーの承認を得る（黙って消さない）"
        ) from exc


def failure_caution(experiment_id: str) -> str:
    """EC-R1・R2・B2 の未達の判定理由に必ず添える注意（自動では NEGATIVE にしない。designer が判断する）。"""
    return (
        "自動の判定は FAIL。次を確かめてから判断する: (1) 検出力不足の可能性: 観測した差・区間の幅を、パイロットで確定した"
        "MDE（W3-A2 の記録）と比べる（MDE 未満の差は否定的結果ではなく検出力不足）。(2) 植込みの効果は操作確認"
        "（ADR-037。c13 の spec_literal の発火数）で確認済みか。実装は正しく手法が主張どおり働かなかったと判断したときは、"
        f'`uv run agenteval exp {experiment_id.lower().replace("-", "_")} --failure-type negative --reason "<理由>"` で'
        "明示する（理由は結果 JSON と結果ページに残る）。"
    )


def failure_override() -> tuple[str, str] | None:
    """CLI の `exp --failure-type negative --reason ...` が環境変数で渡した designer の判断。"""
    kind = os.environ.get("AGENTEVAL_FAILURE_TYPE", "")
    if kind not in ("fail", "negative"):
        return None
    return kind, os.environ.get("AGENTEVAL_FAILURE_REASON", "")


def finalize_coding(
    experiment_id: str,
    metrics: dict[str, Any],
    notes: list[str],
    *,
    seed: int,
    provenance: str,
    method_fallback: str = "",
    negative_if_only: Sequence[str] = (),
    caution: str = "",
    forced_failure: str = "",
    figures: list[tuple[str, str]] | None = None,
    verdict_reason_extra: str = "",
) -> dict[str, Any]:
    """結果 JSON と結果ページを書く。

    判定の種類:
    - 既定は FAIL。`negative_if_only`（EC-B2P・EC-S1 のように原因が機械的に決まる実験だけ）に未達の指標が収まれば NEGATIVE
    - EC-R1・R2・B2 は自動では NEGATIVE にしない。designer が `exp <id> --failure-type negative --reason "<理由>"` で
      明示したときだけ NEGATIVE（理由は結果 JSON の verdict_reason と結果ページに残る）
    - `caution`: 未達のときの判定理由に必ず添える注意（検出力不足の可能性・植込みの効果の確認）
    - `forced_failure`: 基準の指標では表せない欠陥（例: カセットの塩の不備）。基準を満たしていても FAIL にする
      （`result["forced_failure"]`。verify が優先して FAIL にする）。designer の NEGATIVE の指定より優先する
    結果ページは設計者が書いた既存ページの先頭ブロック・5項目・方法・記入済みの気づきを引き継ぐ
    （`carry_over_designer_page`）。
    """
    failed = failing_metrics(experiment_id, metrics)
    failure_type = "fail"
    extra = [verdict_reason_extra] if verdict_reason_extra else []
    if failed and negative_if_only and set(failed) <= set(negative_if_only):
        failure_type = "negative"
    override = failure_override()
    if unevaluable_criteria(experiment_id, metrics):
        # 適用条件の指標の欠落・適用基準 0 件は実装の不備。NEGATIVE の指定より FAIL を優先する
        failure_type = "fail"
        if override is not None:
            extra.append("（--failure-type の指定は、適用条件を評価できないので使われなかった）")
    elif override is not None:
        kind, why = override
        if failed and not forced_failure:
            failure_type = kind
            if kind == "negative":
                extra.append(f"designer の判断で NEGATIVE: {why}")
        elif not failed:
            extra.append("（--failure-type の指定は、未達の基準が無いので使われなかった）")
        else:
            extra.append("（--failure-type の指定は、強制 FAIL があるので使われなかった）")
    if failed and caution:
        extra.append(caution)
    if forced_failure:
        extra.append(f"強制 FAIL: {forced_failure}")
    old_page = pages.RESULTS_DIR / f"{experiment_id}.md"
    old_text = old_page.read_text(encoding="utf-8") if old_page.exists() else None
    result = finalize(
        experiment_id,
        metrics,
        method=design_method(experiment_id) or method_fallback,
        notes=notes,
        figures=figures,
        seed=seed,
        provenance=provenance,
        failure_type=failure_type,
        verdict_reason_extra=" ".join(extra),
    )
    if forced_failure:
        result["forced_failure"] = forced_failure
        result["verdict"] = "FAIL"
        result["failure_type"] = "fail"
        pages.write(meta_of(experiment_id), result)
    carry_over_designer_page(experiment_id, old_text, notes)
    return result


# ---------------------------------------------------------------------------
# Θ 上のベクトル化した計算（EC-R1・EC-R2 の記録のみの指標）
# ---------------------------------------------------------------------------

FloatArray = NDArray[np.float64]


def nv_array(e: VersionEconomics, thetas: ThetaSample) -> FloatArray:
    """NV = H − E[C_del] を Θ の全標本で計算する（`econ.roi.net_value` のベクトル版。単体テストで一致を確かめる）。"""
    a = thetas.arrays
    cost = (
        e.cost_usd
        + e.pi_S * (a.R0 + a.rho * e.diff_lines_S)
        + e.pi_F * (a.R_F + a.H_prime)
        + e.pi_W * (a.R0 + a.rho * e.diff_lines_W + a.r * a.H_prime + (1.0 - a.r) * a.D)
    )
    return np.asarray(a.H - cost, dtype=float)


def breakeven_summary(e: VersionEconomics, thetas: ThetaSample) -> dict[str, Any]:
    """損益分岐の成功率 π_S* の Θ 上の分布。nan の θ を「常に得」「常に損」「定義なし」に分けて割合を記録する。

    `econ.roi.breakeven_success_rate` は解が [0, 1] に無いと nan を返すので、nan の理由はここで再計算して分ける。
    - 常に得: π_S* < 0（π_S = 0 でも NV > 0）
    - 常に損: π_S* > 1（π_S = 1 でも NV < 0）
    - 定義なし: π_W + π_F = 0（比 q が決まらない）か、分母（混合費用 − S の費用）が 0 以下
    分位点は定義のある θ だけで計算し、除いた割合を併記する。
    """
    a = thetas.arrays
    n = len(a.H)
    tail = e.pi_W + e.pi_F
    if tail <= 0.0:
        return {
            "defined": 0.0,
            "always_gain": 0.0,
            "always_loss": 0.0,
            "undefined": 1.0,
            "quantiles": None,
        }
    q = e.pi_W / tail
    c_s = a.R0 + a.rho * e.diff_lines_S
    c_w = a.R0 + a.rho * e.diff_lines_W + a.r * a.H_prime + (1.0 - a.r) * a.D
    c_f = a.R_F + a.H_prime
    mix = q * c_w + (1.0 - q) * c_f
    denom = mix - c_s
    ok = denom > 0.0
    pi_s = np.where(ok, (mix + e.cost_usd - a.H) / np.where(ok, denom, 1.0), np.nan)
    gain = ok & (pi_s < 0.0)
    loss = ok & (pi_s > 1.0)
    defined = ok & (pi_s >= 0.0) & (pi_s <= 1.0)
    qs = (
        {
            f"q{int(p * 100):02d}": float(np.quantile(pi_s[defined], p))
            for p in (0.05, 0.25, 0.5, 0.75, 0.95)
        }
        if defined.any()
        else None
    )
    return {
        "defined": float(defined.sum() / n),
        "always_gain": float(gain.sum() / n),
        "always_loss": float(loss.sum() / n),
        "undefined": float((~ok).sum() / n),
        "quantiles": qs,
    }


def conditional_rob(
    nv_diff: FloatArray, thetas: ThetaSample, *, bins: int = 3
) -> dict[str, list[float]]:
    """各基底パラメータを `bins` 分位に分けたときの条件付き Rob（NV の差 > 0 の割合）。

    パラメータは H・kappa（H'/H）・R0・rho・R_F・D・miss（1 − r）。低い順の分位ごとの値を返す。
    """
    a = thetas.arrays
    columns: dict[str, FloatArray] = {
        "H": a.H,
        "kappa": a.H_prime / a.H,
        "R0": a.R0,
        "rho": a.rho,
        "R_F": a.R_F,
        "D": a.D,
        "miss": 1.0 - a.r,
    }
    positive = nv_diff > 0.0
    out: dict[str, list[float]] = {}
    for name, col in columns.items():
        edges = np.quantile(col, np.linspace(0.0, 1.0, bins + 1))
        idx = np.clip(np.searchsorted(edges, col, side="right") - 1, 0, bins - 1)
        out[name] = [float(positive[idx == b].mean()) for b in range(bins)]
    return out
