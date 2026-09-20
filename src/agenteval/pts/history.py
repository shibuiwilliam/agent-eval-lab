"""変更履歴とテスト履歴（原典 3.4 の特徴量を時系列で作るための土台）。

産業用 PTS（Machalica et al. 2019 ほか）が使う特徴量は、大きく 3 つに分かれる。

1. **変更側**: 変更した要素、変更量（行数・要素数）、変更の種類
2. **テスト側**: そのテストの履歴故障率、実行回数、フレーク率
3. **交差（変更 × テスト）**: 変更した要素とテストが触る要素の距離、
   「この要素が変更されたときこのテストが落ちた回数」

さらに実務で効くのが **ラグ**（recency）である。
「前回このテストを走らせてから何変更経ったか」「前回このテストが落ちてから何変更経ったか」
「この要素が前回変更されてから何変更経ったか」は、どれも強い予測子になる。

**ラグは変更に順序が無いと定義できない。** 改訂前の実装は変更を順序の無い集合として
扱っていたので、これらの特徴量が 1 つも作れていなかった。ここでは変更履歴を
追記型の台帳として持ち、`seq` より前の記録だけから特徴量を作る
（漏洩を構造的に防ぐ。未来の記録は参照できない）。
"""

from __future__ import annotations

import math
import operator
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChangeRecord:
    """変更 1 件の台帳エントリ。"""

    seq: int
    change_id: str
    kind: str
    family: str
    units: set[str]
    churn: dict[str, float] = field(default_factory=dict)


@dataclass
class TestRunRecord:
    """ある変更でテストを走らせた結果。"""

    seq: int
    change_id: str
    task_id: str
    passed: bool
    regressed: bool


@dataclass
class History:
    """追記型の変更履歴・テスト履歴。

    `features(seq, task_id, ...)` は **`seq` より前の記録だけ**を見る。
    """

    changes: list[ChangeRecord] = field(default_factory=list)
    runs: list[TestRunRecord] = field(default_factory=list)
    _by_seq: dict[int, list[TestRunRecord]] = field(default_factory=lambda: defaultdict(list))
    _dirty: bool = True
    _runs_by_task: dict[str, list[TestRunRecord]] = field(default_factory=lambda: defaultdict(list))
    _changes_by_unit: dict[str, list[ChangeRecord]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _runs_by_seq_sorted: dict[int, list[TestRunRecord]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _change_seqs: list[int] = field(default_factory=list)
    _unit_rate_cache: dict[tuple[str, int], float] = field(default_factory=dict)

    def add_change(self, record: ChangeRecord) -> None:
        self.changes.append(record)
        self._dirty = True

    def add_run(self, record: TestRunRecord) -> None:
        self.runs.append(record)
        self._by_seq[record.seq].append(record)
        self._dirty = True

    # --- 索引 -------------------------------------------------------------
    def _index(self) -> None:
        """`seq` 昇順の索引を作る。特徴量計算を O(n^2) から O(log n) にするため。

        台帳は追記型なので、追記があったときだけ作り直す。
        """
        if not self._dirty:
            return
        self._runs_by_task = defaultdict(list)
        for run in sorted(self.runs, key=lambda r: r.seq):
            self._runs_by_task[run.task_id].append(run)
        self._changes_by_unit = defaultdict(list)
        for change in sorted(self.changes, key=lambda c: c.seq):
            for unit in change.units:
                self._changes_by_unit[unit].append(change)
        self._change_seqs = sorted(c.seq for c in self.changes)
        self._runs_by_seq_sorted = defaultdict(list)
        for run in self.runs:
            self._runs_by_seq_sorted[run.seq].append(run)
        self._dirty = False

    @staticmethod
    def _before(items: list[Any], seq: int, key: Any) -> list[Any]:
        """`seq` 未満の要素（`items` は key 昇順であること）。"""
        lo, hi = 0, len(items)
        while lo < hi:
            mid = (lo + hi) // 2
            if key(items[mid]) < seq:
                lo = mid + 1
            else:
                hi = mid
        return items[:lo]

    # --- 特徴量 -----------------------------------------------------------
    def features(self, seq: int, task_id: str, units: set[str]) -> dict[str, float]:
        """`seq` 時点の変更に対する、テスト `task_id` のラグ・履歴特徴。

        `units` は今回の変更が触る要素 `C(Δ)`。
        `seq` より前の記録だけを使う（未来は見えない）。
        """
        self._index()
        by_seq = operator.attrgetter("seq")
        mine = self._before(self._runs_by_task.get(task_id, []), seq, by_seq)
        past_change_count = len(self._before(self._change_seqs, seq, lambda x: x))

        def lag(matching: list[Any]) -> float:
            """直近の該当記録から何変更経ったか。記録が無ければ「見たことがない」印。"""
            if not matching:
                return float(max(1, seq))
            return float(seq - matching[-1].seq)

        ran = mine
        failed = [r for r in mine if not r.passed]
        regressed = [r for r in mine if r.regressed]
        touching: list[ChangeRecord] = []
        for unit in units:
            touching.extend(self._before(self._changes_by_unit.get(unit, []), seq, by_seq))
        touching.sort(key=by_seq)

        n_ran = len(ran)
        out: dict[str, float] = {
            # --- ラグ（recency） ---
            "lag_since_test_ran": lag(ran),
            "lag_since_test_failed": lag(failed),
            "lag_since_test_regressed": lag(regressed),
            "lag_since_units_changed": lag(touching),
            "log_lag_since_test_ran": math.log1p(lag(ran)),
            "log_lag_since_test_regressed": math.log1p(lag(regressed)),
            # --- テスト側の履歴 ---
            "hist_run_count": float(n_ran),
            "hist_fail_rate": len(failed) / n_ran if n_ran else 0.0,
            "hist_regress_count": float(len(regressed)),
            "hist_regress_rate": len(regressed) / n_ran if n_ran else 0.0,
            "never_run_before": 0.0 if n_ran else 1.0,
            # --- 変更側 ---
            "units_change_count": float(len(touching)),
            "history_depth": float(past_change_count),
        }

        # --- 交差: この要素が変更されたとき、このテストはどれだけ落ちたか ---
        touched_seqs = {c.seq for c in touching}
        on_touch = [r for r in mine if r.seq in touched_seqs]
        out["regress_rate_when_units_changed"] = (
            sum(r.regressed for r in on_touch) / len(on_touch) if on_touch else 0.0
        )
        out["n_runs_when_units_changed"] = float(len(on_touch))

        # --- 要素ごとの故障率（このテストに限らない。要素の「危うさ」） ---
        unit_rates = [self.unit_regress_rate(u, seq) for u in units]
        out["unit_regress_rate_max"] = max(unit_rates) if unit_rates else 0.0
        out["unit_regress_rate_mean"] = sum(unit_rates) / len(unit_rates) if unit_rates else 0.0
        return out

    def unit_regress_rate(self, unit: str, seq: int) -> float:
        """要素 `unit` を触った過去の変更で、テストが回帰した割合。"""
        self._index()
        key = (unit, seq)
        cached = self._unit_rate_cache.get(key)
        if cached is not None:
            return cached
        past = self._before(self._changes_by_unit.get(unit, []), seq, operator.attrgetter("seq"))
        rows = [r for c in past for r in self._runs_by_seq_sorted.get(c.seq, [])]
        rate = sum(r.regressed for r in rows) / len(rows) if rows else 0.0
        self._unit_rate_cache[key] = rate
        return rate

    def churn_features(self, record: ChangeRecord) -> dict[str, float]:
        """変更量。そのまま使うと桁が大きいので log も置く。"""
        churn = record.churn or {}
        out: dict[str, float] = {}
        for key in ("lines_added", "lines_removed", "lines_changed", "chars_delta", "n_units"):
            value = float(churn.get(key, 0.0))
            out[f"churn_{key}"] = value
            out[f"churn_log_{key}"] = math.log1p(value)
        out["churn_is_pure_config"] = 1.0 if churn.get("lines_changed", 0.0) == 0.0 else 0.0
        return out


def build_history(changes: list[Any], rows_by_change: dict[str, list[Any]]) -> History:
    """合成変更と (変更, テスト) の行から、台帳を `seq` 順に組み立てる。"""
    history = History()
    for change in sorted(changes, key=lambda c: c.seq):
        history.add_change(
            ChangeRecord(
                seq=change.seq,
                change_id=change.id,
                kind=change.kind,
                family=change.family,
                units=set(change.components),
                churn=dict(change.churn),
            )
        )
        for row in rows_by_change.get(change.id, []):
            history.add_run(
                TestRunRecord(
                    seq=change.seq,
                    change_id=change.id,
                    task_id=row.task_id,
                    passed=not row.label_failed,
                    regressed=bool(row.label_regressed),
                )
            )
    return history
