"""throttlebox のケースバンクの入力（bugsynth.build_casebank が読む。作業ツリーには入らない）。

時刻・補充速度は 2 進数で正確に表せる値（0.25 の倍数など）だけを使い、浮動小数点の誤差を避ける。
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

PACKAGE = "throttlebox"
DESCRIPTION = "レート制限（トークンバケット・窓・クォータ・バックオフ）"
HEADER = ["import pytest"]

TOPICS = {
    "FakeClock.advance": "テスト用時計の進め方",
    "FakeClock.now": "テスト用時計の現在時刻",
    "TokenBucket.reset": "トークンバケットのリセット",
    "TokenBucket.try_acquire": "トークンバケットの取得",
    "TokenBucket.available": "トークンバケットの残量",
    "TokenBucket.time_until": "トークンバケットの待ち時間",
    "SlidingWindowLog.allow": "スライディングウィンドウの許可判定",
    "SlidingWindowLog.remaining": "スライディングウィンドウの残り件数",
    "SlidingWindowLog.retry_in": "スライディングウィンドウの再試行までの秒数",
    "FixedWindowCounter.allow": "固定ウィンドウの許可判定",
    "FixedWindowCounter.remaining": "固定ウィンドウの残り件数",
    "FixedWindowCounter.reset_in": "固定ウィンドウが切り替わるまでの秒数",
    "Quota.check": "クォータの判定",
    "Quota.retry_after": "クォータの再試行までの秒数",
    "Quota.remaining": "クォータの残り",
    "backoff_delays": "バックオフの待ち時間の並び",
    "total_wait": "バックオフの待ち時間の合計",
    "next_delay": "バックオフの 1 回分の待ち時間",
}

# 紛らわしい名前・似た処理の組（変異の「囮あり」の判定に使う。キーは module.qualname）
DECOYS = [
    ["bucket.TokenBucket._refill", "window.FixedWindowCounter._roll"],
    ["window.SlidingWindowLog._evict", "window.FixedWindowCounter._roll"],
    ["units.clamp", "backoff._retry_delay"],
    ["units.elapsed", "window.SlidingWindowLog._evict"],
    ["policy.Quota.retry_after", "backoff._retry_delay"],
    ["units.ceil_div", "policy.Quota.retry_after"],
]

README_REFS = {
    "FakeClock.advance": "時計を進めて、新しい時刻を返す。負の値は `ValueError`（時計は戻せない）",
    "FakeClock.now": "`now()`: 現在時刻（秒）",
    "TokenBucket.reset": "トークン数を `level` にして、その値を返す。`None` は満杯、`capacity` を超える値は `capacity` に、負の値は 0 に切り詰める",
    "TokenBucket.try_acquire": "丁度 `n` 個あれば成功する。`n` が 0 以下なら `ValueError`",
    "TokenBucket.available": "`available()`: 補充後のトークン数",
    "TokenBucket.time_until": "`n` 個たまるまでの秒数。すでに足りていれば `0.0`。`n` が `capacity` を超える、または補充速度が 0 で足りないときは `-1.0`（永久に足りない）",
    "SlidingWindowLog.allow": "丁度 `window_s` 秒前の記録は失効する（窓の外とみなす）",
    "SlidingWindowLog.remaining": "`remaining()`: あと何件許可できるか",
    "SlidingWindowLog.retry_in": "窓に空きがあれば `0.0`、いっぱいなら最も古い記録が失効するまでの秒数",
    "FixedWindowCounter.allow": "窓の開始時刻は `window_s` の倍数（0 秒起点）で、窓が切り替わるとカウントは 0 に戻る",
    "FixedWindowCounter.remaining": "`remaining()`: 窓内の残り許可数",
    "FixedWindowCounter.reset_in": "窓の開始時刻ちょうどなら、窓の長さを切り上げた秒数",
    "Quota.check": "全規則が `cost` を許すときだけ消費して `True`。1 つでも拒否なら何も消費せず `False`",
    "Quota.retry_after": "の最大を整数秒に切り上げる。すべて空いていれば 0",
    "Quota.remaining": "各規則の残りの最小値。バケットは小数部を切り捨てる",
    "backoff_delays": "`jitter` は待ち時間を受け取って返す関数で、`None` は揺らぎなし。揺らぎを掛けた結果も 0 以上 `cap` 以下に収める",
    "total_wait": "`backoff_delays` の合計",
    "next_delay": "`attempt` が負なら `ValueError`",
}

EDGE = {
    "kind": "ambiguous",
    "tag": "edge_task",
    "readme_ref": "バースト上限は `capacity`（満杯のバケットが一度に消費できるトークン数）で、補充速度 `refill_per_sec` とは独立である",
    "prompt": (
        "利用者から、短時間にまとめて送れる量（バースト上限）を今の 2 倍にしてほしいという要望が来ています。"
        "レート制限の設定を見直して、バースト上限が 2 倍になるようにしてください。"
    ),
}


def _c(
    target: str,
    module: str,
    call: str,
    *,
    view: str | None = None,
    setup: tuple[str, ...] = (),
    raises: str | None = None,
    tags: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "target": target,
        "module": module,
        "call": call,
        "view": view,
        "setup": list(setup),
        "raises": raises,
        "tags": list(tags),
    }


def _clock(start: float | None) -> str:
    return "clock = FakeClock()" if start is None else f"clock = FakeClock({start})"


def _bucket(
    cap: float,
    rate: float,
    initial: float | None = None,
    start: float | None = None,
    steps: tuple[str, ...] = (),
) -> tuple[str, ...]:
    extra = "" if initial is None else f", {initial}"
    return (_clock(start), f"bucket = TokenBucket({cap}, {rate}, clock{extra})", *steps)


def _window(
    cls: str, limit: int, window: float, start: float | None, steps: tuple[str, ...]
) -> tuple[str, ...]:
    var = "log" if cls == "SlidingWindowLog" else "counter"
    return (_clock(start), f"{var} = {cls}({limit}, {window}, clock)", *steps)


RULES = {
    "A": "[Rule('bucket', 5, 2.0), Rule('sliding', 4, 8.0)]",
    "B": "[Rule('fixed', 6, 5.0)]",
    "C": "[Rule('bucket', 7, 4.0), Rule('sliding', 5, 8.0), Rule('fixed', 6, 5.0)]",
    "D": "[Rule('bucket', 4, 8.0)]",
    "E": "[Rule('sliding', 3, 12.0)]",
    "F": "[Rule('bucket', 6, 3.0), Rule('fixed', 4, 6.0)]",
}


_KEY_COUNTER = [0]
_KEYS = [
    "acct-318", "mira", "node-b7", "svc-0451", "tok_9z", "client-27", "org-lumen", "east-4", "pj-aurora",
    "dev-612", "hub-33", "ws-kappa", "ten-5821", "lane-9", "cell-q4", "uid-7315", "zone-r2", "app-hex",
    "team-orca", "api-v3x", "grp-1187", "site-nu", "box-704", "srv-tau", "kid-262", "ops-mag", "reg-87",
    "pid-5530", "net-ion", "usr-3719", "row-w8", "vm-1642", "key-sig", "ch-0993", "lab-yew", "gw-4408",
]


def _rekey(setup: tuple[str, ...], call: str) -> tuple[tuple[str, ...], str]:
    """Quota のケースの key をケースごとに一意な値に置き換える（識別的リテラルを共有させない）。"""
    olds = sorted(set(re.findall(r"quota\.\w+\('([^']+)'", " ".join((*setup, call)))))
    for old in olds:
        new = _KEYS[_KEY_COUNTER[0] % len(_KEYS)]
        _KEY_COUNTER[0] += 1
        setup = tuple(x.replace(f"'{old}'", f"'{new}'") for x in setup)
        call = call.replace(f"'{old}'", f"'{new}'")
    return setup, call


def _quota(rules: str, start: float | None = None, steps: tuple[str, ...] = ()) -> tuple[str, ...]:
    return (_clock(start), f"quota = Quota({RULES[rules]}, clock)", *steps)


def inputs(rng: np.random.Generator) -> list[dict[str, Any]]:
    cs: list[dict[str, Any]] = []
    # --- FakeClock ---
    for start, sec in [(17.25, 2.5), (17.25, 0.0), (None, 0.25), (1000.5, 37.75), (None, 3.5)]:
        setup = (_clock(start),)
        cs.append(_c("FakeClock.advance", "clock", f"clock.advance({sec})", setup=setup, tags=("default",) if start is None else ()))
    cs.append(_c("FakeClock.advance", "clock", "clock.advance(-0.25)", setup=(_clock(17.25),), raises="ValueError", tags=("boundary",)))
    cs.append(_c("FakeClock.advance", "clock", "clock.advance(4.5)", setup=(_clock(2.25), "clock.advance(1.5)", "clock.advance(0.75)")))
    for start, steps in [(None, ()), (37.5, ("clock.advance(2.25)",))]:
        cs.append(_c("FakeClock.now", "clock", "clock.now()", setup=(_clock(start), *steps)))
    # --- TokenBucket.reset ---
    for cap, rate, init, steps, level in [
        (7, 2.5, None, (), None),
        (7, 2.5, None, (), 3.5),
        (7, 2.5, None, (), 9.25),
        (7, 2.5, None, (), -1.5),
        (7, 2.5, None, (), 7),
        (7.5, 0.5, 2.25, ("clock.advance(4.0)",), None),
        (4.25, 1.5, None, ("bucket.try_acquire(2.5)",), None),
        (6, 1.75, None, (), 0),
        (13.5, 4.0, 5, (), 13.75),
    ]:
        arg = "" if level is None else str(level)
        tags = ("default",) if level is None else ()
        cs.append(_c("TokenBucket.reset", "bucket", f"bucket.reset({arg})", setup=_bucket(cap, rate, init, None, steps), tags=tags))
    # --- TokenBucket.try_acquire ---
    adv15 = ("clock.advance(1.5)",)
    for cap, rate, init, start, steps, n in [
        (7, 2.5, None, None, (), "7"),
        (7, 2.5, 3, None, (), "3"),
        (7, 2.5, 3, None, (), "3.25"),
        (7, 2.5, 0, 17.25, adv15, "3"),
        (7, 2.5, 0, 17.25, adv15, "3.75"),
        (7, 2.5, 0, 17.25, adv15, "4"),
        (7, 2.5, 0, None, ("clock.advance(100.0)",), "7"),
        (7, 2.5, 0, None, ("clock.advance(100.0)",), "7.25"),
        (7, 2.5, None, None, (), "8"),
        (7, 2.5, 0.75, None, (), ""),
        (7, 2.5, 1.5, None, (), ""),
        (7, 0, 2.5, None, ("clock.advance(50.0)",), "3"),
        (7, 0, 3.5, None, ("clock.advance(50.0)",), "3"),
        (5, 1.25, None, None, ("bucket.try_acquire(3)",), "3"),
        (5, 1.25, None, None, ("bucket.try_acquire(3)", "clock.advance(2.0)"), "4"),
        (3.5, 0.5, 1.25, 37.5, ("clock.advance(0.5)",), "1.5"),
        (11.5, 3.0, 6, None, ("bucket.try_acquire(6)", "clock.advance(1.25)"), "3.75"),
        (9, 0.75, 9, 21.25, ("bucket.try_acquire(4.5)", "clock.advance(2.0)"), "6"),
    ]:
        tags = ("default",) if n == "" else ()
        cs.append(_c("TokenBucket.try_acquire", "bucket", f"bucket.try_acquire({n})", setup=_bucket(cap, rate, init, start, steps), tags=tags))
    cs.append(_c("TokenBucket.try_acquire", "bucket", "bucket.try_acquire(0)", setup=_bucket(7, 2.5), raises="ValueError", tags=("boundary",)))
    cs.append(_c("TokenBucket.try_acquire", "bucket", "bucket.try_acquire(-1.5)", setup=_bucket(7, 2.5), raises="ValueError"))
    # --- TokenBucket.available ---
    for cap, rate, init, start, steps in [
        (7, 2.5, 3, None, ()),
        (7, 2.5, 0, 17.25, adv15),
        (7, 2.5, 0, 17.25, ("clock.advance(0.25)",)),
        (7, 2.5, 0, None, ("clock.advance(100.0)",)),
        (7, 0, 2.5, None, ("clock.advance(50.0)",)),
        (7, 2.5, None, None, ("bucket.try_acquire(4.5)",)),
        (7, 2.5, None, None, ("bucket.try_acquire(4.5)", "clock.advance(1.0)")),
        (3.5, 0.5, 1.25, 37.5, ("clock.advance(2.5)",)),
        (13.5, 4.0, 0, None, ("clock.advance(1.75)", "bucket.try_acquire(2.5)")),
        (5, 1.25, None, None, ("bucket.reset(1.5)", "clock.advance(2.0)")),
        (5, 1.25, 2.5, None, ("clock.advance(2.0)", "clock.advance(0.25)")),
    ]:
        cs.append(_c("TokenBucket.available", "bucket", "bucket.available()", setup=_bucket(cap, rate, init, start, steps)))
    cs.append(_c("TokenBucket.available", "bucket", "bucket.available()", setup=_bucket(7, 2.5), tags=("edge_task",)))
    # --- TokenBucket.time_until ---
    for cap, rate, init, start, steps, n in [
        (7, 2.5, None, None, (), "3"),
        (7, 2.5, 3, None, (), "3"),
        (7, 2.5, 0, None, (), "5"),
        (7, 2.5, 0, None, (), "6"),
        (7, 2.5, 1.25, 17.25, ("clock.advance(0.5)",), "4"),
        (7, 2.5, 0, None, (), "8"),
        (7, 0, 1, None, (), "3"),
        (7, 0, 5, None, (), "3"),
        (7, 2.5, 0.5, None, (), ""),
        (7, 2.5, 1.5, None, (), ""),
        (9, 0.75, 9, 21.25, ("bucket.try_acquire(9)", "clock.advance(2.0)"), "4.5"),
        (6, 1.5, 0, None, ("clock.advance(2.0)",), "5.25"),
        (7, 0, 0.25, 4.5, ("clock.advance(9.0)",), "0.5"),
        (7, 0, 6.75, None, (), "6.5"),
        (5.5, 0, 0, None, (), "4.75"),
        (7, 0, 2.25, None, ("bucket.try_acquire(1.25)",), "1.5"),
    ]:
        tags = ("default",) if n == "" else ()
        cs.append(_c("TokenBucket.time_until", "bucket", f"bucket.time_until({n})", setup=_bucket(cap, rate, init, start, steps), tags=tags))
    cs.append(_c("TokenBucket.time_until", "bucket", "bucket.time_until(0)", setup=_bucket(7, 2.5), raises="ValueError", tags=("boundary",)))
    # --- SlidingWindowLog ---
    allow3 = ("log.allow()", "log.allow()", "log.allow()")
    for limit, window, start, steps, cost in [
        (3, 10.0, None, (), ""),
        (3, 10.0, None, allow3, ""),
        (2, 10.0, 5.25, ("log.allow()", "log.allow()", "clock.advance(10.0)"), ""),
        (2, 10.0, 5.25, ("log.allow()", "log.allow()", "clock.advance(9.75)"), ""),
        (2, 10.0, 5.25, ("log.allow()", "clock.advance(4.0)", "log.allow()", "clock.advance(6.0)"), ""),
        (2, 10.0, 5.25, ("log.allow()", "clock.advance(4.0)", "log.allow()", "clock.advance(5.75)"), ""),
        (5, 8.0, None, allow3, "2"),
        (5, 8.0, None, allow3, "3"),
        (5, 8.0, None, (), "5"),
        (5, 8.0, None, (), "6"),
        (1, 4.5, 17.25, ("log.allow()",), ""),
        (1, 4.5, 17.25, ("log.allow()", "clock.advance(4.5)"), ""),
        (4, 7.5, 3.25, ("log.allow(2)", "clock.advance(3.75)", "log.allow(2)", "clock.advance(3.75)"), "2"),
        (4, 7.5, 3.25, ("log.allow(2)", "clock.advance(3.75)", "log.allow(2)", "clock.advance(3.5)"), "2"),
    ]:
        arg = cost
        cs.append(_c("SlidingWindowLog.allow", "window", f"log.allow({arg})", setup=_window("SlidingWindowLog", limit, window, start, steps), tags=("default",) if arg == "" else ()))
    for limit, window, start, steps in [
        (3, 10.0, None, ()),
        (3, 10.0, None, ("log.allow()", "log.allow()")),
        (3, 10.0, None, allow3),
        (2, 10.0, 5.25, ("log.allow()", "log.allow()", "clock.advance(10.0)")),
        (2, 10.0, 5.25, ("log.allow()", "log.allow()", "clock.advance(9.75)")),
        (4, 7.5, 3.25, ("log.allow(2)", "clock.advance(3.75)", "log.allow(2)")),
    ]:
        cs.append(_c("SlidingWindowLog.remaining", "window", "log.remaining()", setup=_window("SlidingWindowLog", limit, window, start, steps)))
    for limit, window, start, steps in [
        (3, 10.0, None, ()),
        (3, 10.0, None, ("log.allow()", "log.allow()")),
        (3, 12.0, 8.25, ("log.allow()", "clock.advance(1.5)", "log.allow()", "clock.advance(1.0)", "log.allow()", "clock.advance(2.0)")),
        (3, 12.0, 8.25, ("log.allow()", "clock.advance(1.5)", "log.allow()", "clock.advance(1.0)", "log.allow()", "clock.advance(9.5)")),
        (3, 12.0, 8.25, ("log.allow()", "clock.advance(1.5)", "log.allow()", "clock.advance(1.0)", "log.allow()", "clock.advance(12.0)")),
        (2, 7.5, 3.25, ("log.allow()", "log.allow()", "clock.advance(7.25)")),
        (1, 4.5, 17.25, ("log.allow()",)),
    ]:
        cs.append(_c("SlidingWindowLog.retry_in", "window", "log.retry_in()", setup=_window("SlidingWindowLog", limit, window, start, steps)))
    # --- FixedWindowCounter ---
    fall3 = ("counter.allow()", "counter.allow()", "counter.allow()")
    for limit, window, start, steps, cost in [
        (3, 5.0, 17.25, (), ""),
        (3, 5.0, 17.25, fall3, ""),
        (3, 5.0, 17.25, (*fall3, "clock.advance(2.75)"), ""),
        (3, 5.0, 17.25, (*fall3, "clock.advance(2.5)"), ""),
        (6, 8.0, 3.5, ("counter.allow(2)",), "4"),
        (6, 8.0, 3.5, ("counter.allow(2)",), "5"),
        (6, 8.0, 3.5, (), "6"),
        (6, 8.0, 3.5, (), "7"),
        (4, 7.5, 9.25, ("counter.allow(4)", "clock.advance(5.5)"), "3"),
        (4, 7.5, 9.25, ("counter.allow(4)", "clock.advance(5.25)"), "3"),
        (1, 12.0, 30.25, ("counter.allow()", "clock.advance(5.75)"), ""),
        (1, 12.0, 30.25, ("counter.allow()", "clock.advance(6.0)"), ""),
    ]:
        cs.append(_c("FixedWindowCounter.allow", "window", f"counter.allow({cost})", setup=_window("FixedWindowCounter", limit, window, start, steps), tags=("default",) if cost == "" else ()))
    for limit, window, start, steps in [
        (3, 5.0, 17.25, ()),
        (3, 5.0, 17.25, ("counter.allow()", "counter.allow()")),
        (3, 5.0, 17.25, (*fall3, "clock.advance(2.75)")),
        (8, 12.0, 41.5, ("counter.allow(3)", "clock.advance(6.5)", "counter.allow(2)")),
        (8, 12.0, 41.5, ("counter.allow(3)", "clock.advance(6.5)", "counter.allow(2)", "clock.advance(5.5)")),
    ]:
        cs.append(_c("FixedWindowCounter.remaining", "window", "counter.remaining()", setup=_window("FixedWindowCounter", limit, window, start, steps)))
    for limit, window, start, steps in [
        (3, 5.0, 17.25, ()),
        (3, 5.0, 20.0, ()),
        (3, 5.0, 17.25, ("clock.advance(2.5)",)),
        (3, 5.0, 17.25, ("clock.advance(2.75)",)),
        (3, 7.5, 9.25, ()),
        (3, 7.5, 15.0, ()),
        (3, 12.0, 30.25, ("clock.advance(11.5)",)),
        (3, 12.0, 30.25, ("counter.allow()", "clock.advance(0.5)")),
    ]:
        cs.append(_c("FixedWindowCounter.reset_in", "window", "counter.reset_in()", setup=_window("FixedWindowCounter", limit, window, start, steps)))
    # --- Quota.check ---
    for rules, start, steps, key, cost in [
        ("A", None, (), "user-42", ""),
        ("A", None, (), "user-42", "3"),
        ("A", None, (), "user-42", "4"),
        ("A", None, (), "user-42", "5"),
        ("A", None, ("quota.check('user-42', 3)",), "user-42", "2"),
        ("A", None, ("quota.check('user-42', 3)",), "user-42", "1"),
        ("A", None, ("quota.check('user-42', 3)", "quota.check('user-42', 5)"), "user-42", "1"),
        ("A", None, ("quota.check('user-42', 3)",), "user-77", "4"),
        ("A", 13.25, ("quota.check('user-42', 4)", "clock.advance(8.0)"), "user-42", "4"),
        ("A", 13.25, ("quota.check('user-42', 4)", "clock.advance(7.75)"), "user-42", "4"),
        ("B", 17.25, ("quota.check('k-9', 6)",), "k-9", ""),
        ("B", 17.25, ("quota.check('k-9', 4)",), "k-9", "2"),
        ("B", 17.25, ("quota.check('k-9', 4)",), "k-9", "3"),
        ("B", 17.25, ("quota.check('k-9', 6)", "clock.advance(2.75)"), "k-9", "6"),
        ("C", 4.5, (), "tenant-8", "6"),
        ("C", 4.5, (), "tenant-8", "7"),
        ("C", 4.5, ("quota.check('tenant-8', 5)",), "tenant-8", "2"),
        ("C", 4.5, ("quota.check('tenant-8', 5)", "clock.advance(0.5)"), "tenant-8", ""),
        ("D", None, ("quota.check('a-1', 4)", "clock.advance(2.0)"), "a-1", "1"),
        ("D", None, ("quota.check('a-1', 4)", "clock.advance(1.75)"), "a-1", "1"),
        ("E", 26.5, ("quota.check('x-3')", "quota.check('x-3')", "quota.check('x-3')"), "x-3", ""),
        ("F", 11.75, ("quota.check('p-5', 4)", "clock.advance(1.0)"), "p-5", "3"),
    ]:
        call = f"quota.check('{key}'" + (f", {cost})" if cost else ")")
        qs, call = _rekey(_quota(rules, start, steps), call)
        cs.append(_c("Quota.check", "policy", call, setup=qs, tags=("default",) if not cost else ()))
    cs.append(_c("Quota.check", "policy", "quota.check('user-42', 3)", setup=_quota("A"), tags=("edge_task",)))
    # --- Quota.retry_after ---
    for rules, start, steps, key in [
        ("A", None, (), "u-1"),
        ("D", None, ("quota.check('u-1', 4)",), "u-1"),
        ("D", None, ("quota.check('u-1', 4)", "clock.advance(0.5)"), "u-1"),
        ("D", None, ("quota.check('u-1', 4)", "clock.advance(1.25)"), "u-1"),
        ("D", None, ("quota.check('u-1', 4)", "clock.advance(2.0)"), "u-1"),
        ("E", 26.5, ("quota.check('x-3')", "clock.advance(2.5)", "quota.check('x-3')", "quota.check('x-3')"), "x-3"),
        ("E", 26.5, ("quota.check('x-3', 3)", "clock.advance(11.75)"), "x-3"),
        ("B", 17.25, ("quota.check('k-9', 6)",), "k-9"),
        ("B", 17.25, ("quota.check('k-9', 5)",), "k-9"),
        ("F", 11.75, ("quota.check('p-5', 4)",), "p-5"),
        ("F", 11.75, ("quota.check('p-5', 4)", "clock.advance(2.5)"), "p-5"),
        ("C", 4.5, ("quota.check('tenant-8', 5)",), "tenant-8"),
    ]:
        qs, call = _rekey(_quota(rules, start, steps), f"quota.retry_after('{key}')")
        cs.append(_c("Quota.retry_after", "policy", call, setup=qs))
    # --- Quota.remaining ---
    for rules, start, steps, key in [
        ("A", None, (), "u-1"),
        ("A", None, ("quota.check('u-1', 3)",), "u-1"),
        ("A", None, ("quota.check('u-1', 3)",), "u-2"),
        ("B", 17.25, ("quota.check('k-9', 4)",), "k-9"),
        ("B", 17.25, ("quota.check('k-9', 4)", "clock.advance(2.75)"), "k-9"),
        ("C", 4.5, ("quota.check('tenant-8', 2)",), "tenant-8"),
        ("C", 4.5, ("quota.check('tenant-8', 3)", "clock.advance(1.5)"), "tenant-8"),
        ("D", None, ("quota.check('a-1', 3)", "clock.advance(1.5)"), "a-1"),
        ("E", 26.5, ("quota.check('x-3')", "clock.advance(12.0)"), "x-3"),
        ("F", 11.75, ("quota.check('p-5', 4)", "clock.advance(1.25)"), "p-5"),
    ]:
        qs, call = _rekey(_quota(rules, start, steps), f"quota.remaining('{key}')")
        cs.append(_c("Quota.remaining", "policy", call, setup=qs))
    # --- backoff ---
    jit_half = ("jitter = lambda d: d / 2",)
    jit_big = ("jitter = lambda d: d * 3",)
    jit_neg = ("jitter = lambda d: d - 3.5",)
    for call, setup in [
        ("backoff_delays(0)", ()),
        ("backoff_delays(5)", ()),
        ("backoff_delays(8)", ()),
        ("backoff_delays(4, 0.75, 3.0)", ()),
        ("backoff_delays(4, base=7.5, factor=2.0, cap=30.0)", ()),
        ("backoff_delays(6, 7.5, 2.0, 30.0)", ()),
        ("backoff_delays(4, 0.75, 2.0, 30.0, jitter)", jit_half),
        ("backoff_delays(4, 0.75, 2.0, 5.0, jitter)", jit_big),
        ("backoff_delays(4, 1.5, 2.0, 30.0, jitter)", jit_neg),
        ("backoff_delays(5, jitter=jitter)", jit_half),
        ("backoff_delays(3, 2.5, 1.5, 4.5)", ()),
        ("backoff_delays(3, 12.0, 1.0, 12.0)", ()),
        ("backoff_delays(4, 0.25, 2.5, 45.5)", ()),
    ]:
        cs.append(_c("backoff_delays", "backoff", call, setup=setup, tags=("default",) if call.count(",") == 0 else ()))
    bases = [0.25, 0.5, 0.75, 1.5, 2.5]
    factors = [1.5, 2.0, 2.5, 3.0]
    caps = [7.5, 12.0, 30.0, 45.5]
    for _ in range(2):
        n = int(rng.integers(3, 8))
        b, f, cap = bases[int(rng.integers(5))], factors[int(rng.integers(4))], caps[int(rng.integers(4))]
        cs.append(_c("backoff_delays", "backoff", f"backoff_delays({n}, {b}, {f}, {cap})"))
    for call, setup in [
        ("total_wait(0)", ()),
        ("total_wait(7)", ()),
        ("total_wait(4, 0.75, 3.0)", ()),
        ("total_wait(6, 7.5, 2.0, 30.0)", ()),
        ("total_wait(4, 0.75, 2.0, 30.0, jitter)", jit_half),
        ("total_wait(4, 0.75, 2.0, 5.0, jitter)", jit_big),
        ("total_wait(4, 1.5, 2.0, 30.0, jitter)", jit_neg),
        ("total_wait(5, jitter=jitter)", jit_half),
        ("total_wait(3, 12.0, 1.0, 12.0)", ()),
    ]:
        cs.append(_c("total_wait", "backoff", call, setup=setup, tags=("default",) if call.count(",") == 0 else ()))
    for call, raises in [
        ("next_delay(0)", None),
        ("next_delay(3)", None),
        ("next_delay(4, 0.75, 3.0)", None),
        ("next_delay(2, 7.5, 2.0, 30.0)", None),
        ("next_delay(3, 7.5, 2.0, 30.0)", None),
        ("next_delay(3, 7.5, 2.0, 60.0)", None),
        ("next_delay(5, base=0.25, factor=2.5, cap=45.5)", None),
        ("next_delay(2, 2.5, 1.5, 4.5)", None),
        ("next_delay(-1)", "ValueError"),
    ]:
        cs.append(_c("next_delay", "backoff", call, raises=raises, tags=("default",) if call.count(",") == 0 and raises is None else ()))
    return cs
