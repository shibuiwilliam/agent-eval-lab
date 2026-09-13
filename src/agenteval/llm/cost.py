"""単価・コスト台帳・予算ガード。単価は pricing.yaml からだけ読む。"""

from __future__ import annotations

import json
import os
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
        self.spent = sum(e.usd for e in read_ledger(ledger_path))
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


def now_iso() -> str:
    """台帳用のタイムスタンプ。"""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def dumps(obj: Any) -> str:
    """デバッグ用の短い JSON。"""
    return json.dumps(obj, ensure_ascii=False)[:2000]
