"""E8-1 模擬忠実度と TTL（原典 8.5）。"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from _common import finalize, labeled

from agenteval.drift import fidelity as fidelity_mod
from agenteval.env.external import ExternalService
from agenteval.reports import plotting

METHOD = """外部サービス v1 の応答をカセットに記録し（`data/cassettes/external/`）、
その後 `DriftInjector.bump_external(v2)` 相当の変更（`price` → `unit_price` への改名と値の変化）を
起こしたサービスと比較して忠実度 `F` を出した（原典 8.5）。
一致判定は層化アサーション（厳密一致 → 構造一致 → 意味的判定）を順に適用し、どの層で判定したかを数える。
TTL は記録日から `ttl_days` を過ぎたカセットを期限切れとし、意図的に期限切れのカセットを作って
検出率を測った。この実験は LLM を使わない。"""


def main(live: bool = False, seed: int = 20260913) -> dict[str, Any]:
    directory = Path("data/cassettes/external_e8_1")
    fidelity_mod.record_cassettes(
        ExternalService("v1"), ttl_days=30, recorded_at="2026-09-01", directory=directory
    )
    today = date(2026, 9, 13)

    before = fidelity_mod.fidelity_job(ExternalService("v1"), directory, today)
    after = fidelity_mod.fidelity_job(ExternalService("v2"), directory, today)

    expired_dir = Path("data/cassettes/external_e8_1_expired")
    fidelity_mod.record_cassettes(
        ExternalService("v1"), ttl_days=5, recorded_at="2026-08-01", directory=expired_dir
    )
    ttl_rate = fidelity_mod.expired_detection_rate(expired_dir, today)
    expired_job = fidelity_mod.fidelity_job(ExternalService("v1"), expired_dir, today)

    changed_keys = {"price", "unit_price"}
    mismatch_ok = int(after.mismatched_keys == changed_keys)

    fig = plotting.bar_compare(
        "E8-1",
        "fidelity",
        "外部サービスの版と模擬忠実度 F",
        ["v1（変更前）", "v2（変更後）"],
        [before.fidelity, after.fidelity],
        "F",
        "simulated",
        threshold=0.5,
    )

    metrics = {
        "fidelity_before": labeled(before.fidelity),
        "fidelity_after": labeled(after.fidelity),
        "mismatch_keys_match": labeled(mismatch_ok),
        "ttl_detection_rate": labeled(ttl_rate),
        "n_cassettes": labeled(before.checked),
        "expired_count": labeled(len(expired_job.expired)),
        "layer_exact_before": labeled(before.by_layer.get("exact", 0)),
        "layer_semantic_after": labeled(after.by_layer.get("semantic", 0)),
    }
    notes = [
        f"不一致キー: {sorted(after.mismatched_keys)}（変更したキー: {sorted(changed_keys)}）",
        f"層ごとの判定件数（変更後）: {after.by_layer}",
        (
            "キー名が変わる変更なので、層化アサーションでは「構造一致」にも到達せず"
            "「意味的判定」の層に落ちる。値だけが変わる変更なら構造一致の層で捕まる。"
        ),
        (
            "この実験は外部サービスをこちらで定義しているので、「実際のサービスが変わった」ことの"
            "検証ではない。検証したのは F の計算と不一致キーの特定、TTL 判定が正しく動くことである。"
        ),
    ]
    return finalize("E8-1", metrics, METHOD, notes, figures=[("忠実度", fig)], seed=seed)


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))
