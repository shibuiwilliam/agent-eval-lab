"""単価・コスト台帳・予算ガード。単価は pricing.yaml からだけ読む。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from agenteval.core.schema import Usage

REPO_ROOT = Path(__file__).resolve().parents[3]
PRICING_PATH = REPO_ROOT / "pricing.yaml"
LEDGER_PATH = REPO_ROOT / "data" / "cost_ledger.jsonl"


class BudgetExceeded(RuntimeError):
    """予算を超えるため呼び出しを行わなかった、という信号。"""


class Pricing(BaseModel):
    """pricing.yaml の内容。"""

    verified_at: str
    source: str
    models: dict[str, dict[str, float]]
    batch_discount: float = 0.5

    def age_days(self, today: date | None = None) -> int:
        ref = today or datetime.now(tz=UTC).date()
        return (ref - date.fromisoformat(self.verified_at)).days

    def usd(self, model: str, usage: Usage) -> float:
        """使用量 → USD。未知のモデルは 0 にせず例外にする（黙って過小評価しない）。"""
        if model not in self.models:
            raise KeyError(f"pricing.yaml に単価が無いモデル: {model}")
        p = self.models[model]
        return (
            usage.input_tokens * p["input"]
            + usage.output_tokens * p["output"]
            + usage.cache_creation_input_tokens * p.get("cache_write_5m", p["input"])
            + usage.cache_read_input_tokens * p.get("cache_read", p["input"])
        ) / 1_000_000


_PRICING: Pricing | None = None


def load_pricing(path: Path | None = None) -> Pricing:
    """pricing.yaml を読む（プロセス内でキャッシュする）。"""
    global _PRICING
    if _PRICING is None or path is not None:
        raw = yaml.safe_load((path or PRICING_PATH).read_text(encoding="utf-8"))
        pricing = Pricing.model_validate(raw)
        if path is not None:
            return pricing
        _PRICING = pricing
    return _PRICING


class LedgerEntry(BaseModel):
    """台帳 1 行。replay / sim の行は usd = 0 で記録する。"""

    ts: str
    run_id: str
    step: int
    model: str
    mode: str
    usd: float
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int


def append_ledger(entry: LedgerEntry, path: Path | None = None) -> None:
    """台帳に 1 行追記する。"""
    target = path or LEDGER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(entry.model_dump_json() + "\n")


def read_ledger(path: Path | None = None) -> list[LedgerEntry]:
    """台帳を全部読む。無ければ空。"""
    target = path or LEDGER_PATH
    if not target.exists():
        return []
    out: list[LedgerEntry] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(LedgerEntry.model_validate_json(line))
    return out


_HEAD_BYTES = 4096


class LedgerTotalCache(BaseModel):
    """台帳の総額のキャッシュ。`<ledger>.total.json` に置く（例: data/cost_ledger.jsonl.total.json）。"""

    ledger: str  # 台帳の絶対パス
    offset: int  # 集計済みのバイト数。必ず改行の直後
    usd: float  # offset までの usd の総和
    lines: int  # offset までの台帳エントリ数（空行は数えない）
    head_sha256: str  # 台帳の先頭 min(offset, 4096) バイトの sha256（書き換えの検出）


def _cache_path(ledger: Path) -> Path:
    return ledger.with_name(ledger.name + ".total.json")


def _head_sha256(ledger: Path, offset: int) -> str:
    with ledger.open("rb") as fh:
        head = fh.read(min(offset, _HEAD_BYTES))
    return hashlib.sha256(head).hexdigest()


def _read_valid_cache(ledger: Path, size: int) -> LedgerTotalCache | None:
    """使えるキャッシュだけ返す。壊れている・台帳が縮んだ・先頭が変わったなら None。"""
    cache_file = _cache_path(ledger)
    try:
        cache = LedgerTotalCache.model_validate_json(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if cache.ledger != str(ledger) or cache.offset < 0 or cache.offset > size:
        return None
    if _head_sha256(ledger, cache.offset) != cache.head_sha256:
        return None
    return cache


def ledger_total_usd(path: Path | None = None, *, use_cache: bool = True) -> float:
    """台帳の usd の総和。

    path is None なら呼び出し時点の LEDGER_PATH を使う（conftest の差し替えが効くように、既定値に束縛しない）。
    キャッシュがあり、台帳のサイズ >= offset かつ head_sha256 が一致すれば、offset から末尾までだけを読んで足す。
    一致しなければ全件を読み直す。末尾の改行で終わらない行（書き込み途中）は集計しない。
    集計後、キャッシュを一時ファイル＋os.replace で書き戻す（並列プロセスでは最後の書き手が勝つ。どれも整合した組）。
    不正な行は ValueError（read_ledger と同じく黙って飛ばさない）。
    不変条件: 戻り値 == sum(e.usd for e in read_ledger(path))（行の順に足すので一致する）。

    限界: 先頭 4096 バイトより後ろで、集計済み範囲の内容だけを同じサイズ以上のまま書き換えた場合は検出できない
    （台帳は追記専用の前提）。疑うときは use_cache=False で全件を読む。
    """
    ledger = (path or LEDGER_PATH).resolve()
    if not ledger.exists():
        return 0.0
    size = ledger.stat().st_size
    cache = _read_valid_cache(ledger, size) if use_cache else None
    start, usd, lines = (cache.offset, cache.usd, cache.lines) if cache else (0, 0.0, 0)
    with ledger.open("rb") as fh:
        fh.seek(start)
        data = fh.read()
    end = data.rfind(b"\n") + 1  # 改行の直後まで。改行が無ければ 0（何も集計しない）
    for raw in data[:end].splitlines():
        if raw.strip():
            usd += LedgerEntry.model_validate_json(raw).usd
            lines += 1
    offset = start + end
    if use_cache and (cache is None or offset != cache.offset):
        new_cache = LedgerTotalCache(
            ledger=str(ledger),
            offset=offset,
            usd=usd,
            lines=lines,
            head_sha256=_head_sha256(ledger, offset),
        )
        cache_file = _cache_path(ledger)
        tmp = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(new_cache.model_dump_json(), encoding="utf-8")
            os.replace(tmp, cache_file)
        except OSError:
            # キャッシュは高速化のためだけ。書けなくても総額は正しいので続行する
            tmp.unlink(missing_ok=True)
    return usd


def ledger_summary(path: Path | None = None) -> dict[str, Any]:
    """累積コスト・呼び出し数・cache hit 率。"""
    entries = read_ledger(path)
    usd = sum(e.usd for e in entries)
    cache_read = sum(e.cache_read_input_tokens for e in entries)
    input_tokens = sum(e.input_tokens for e in entries)
    live_calls = [e for e in entries if e.mode in ("live", "record")]
    denom = cache_read + input_tokens
    return {
        "calls": len(entries),
        "live_calls": len(live_calls),
        "usd": round(usd, 4),
        "input_tokens": input_tokens,
        "output_tokens": sum(e.output_tokens for e in entries),
        "cache_read_input_tokens": cache_read,
        "cache_hit_ratio": round(cache_read / denom, 4) if denom else 0.0,
        "by_mode": {
            mode: len([e for e in entries if e.mode == mode])
            for mode in sorted({e.mode for e in entries})
        },
    }


class BudgetGuard:
    """呼び出し前に「累積 ＋ 直近平均コスト」が予算を超えるなら止める。"""

    def __init__(self, budget_usd: float | None = None, ledger_path: Path | None = None) -> None:
        env = os.environ.get("AGENTEVAL_BUDGET_USD")
        self.budget = budget_usd if budget_usd is not None else (float(env) if env else None)
        self.ledger_path = ledger_path
        self.spent = ledger_total_usd(ledger_path)
        self._recent: list[float] = []

    def check(self) -> None:
        """live 呼び出しの直前に呼ぶ。超えるなら BudgetExceeded。"""
        if self.budget is None:
            raise BudgetExceeded("AGENTEVAL_BUDGET_USD が未設定のため live 呼び出しを行いません")
        avg = sum(self._recent) / len(self._recent) if self._recent else 0.01
        if self.spent + avg > self.budget:
            raise BudgetExceeded(
                f"予算停止: spent={self.spent:.4f} + est={avg:.4f} > budget={self.budget:.2f}"
            )

    def record(self, usd: float) -> None:
        """実際にかかったコストを反映する。"""
        self.spent += usd
        self._recent.append(usd)
        if len(self._recent) > 20:
            self._recent.pop(0)


def experiment_budget(
    entry: Mapping[str, Any],
    *,
    ledger_path: Path | None = None,
) -> BudgetGuard:
    """registry.yaml の 1 項目の budget_usd を強制する BudgetGuard を返す。

    実験の上限 = 現在の累積 + entry["budget_usd"]（無ければ 0）。
    累積の上限 = AGENTEVAL_BUDGET_USD（未設定なら None）。
    budget は累積の上限が None なら None（check() は従来どおり「未設定」で止める）、
    そうでなければ min(実験の上限, 累積の上限)。
    budget_usd == 0 の実験は budget == 現在の累積となり、最初の live 呼び出しの前に止まる。
    返した 1 個の guard を、その実験の全 run の LLMClient(budget=...) に共有させる。
    """
    per_experiment = float(entry.get("budget_usd", 0))
    if per_experiment < 0:
        raise ValueError(f"budget_usd は 0 以上: {per_experiment}")
    env = os.environ.get("AGENTEVAL_BUDGET_USD")
    cumulative_cap = float(env) if env else None
    spent0 = ledger_total_usd(ledger_path)
    budget = None if cumulative_cap is None else min(spent0 + per_experiment, cumulative_cap)
    return BudgetGuard(budget_usd=budget, ledger_path=ledger_path)


def now_iso() -> str:
    """台帳用のタイムスタンプ。"""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def dumps(obj: Any) -> str:
    """デバッグ用の短い JSON。"""
    return json.dumps(obj, ensure_ascii=False)[:2000]
