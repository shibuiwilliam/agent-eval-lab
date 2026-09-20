# 検証サマリ

*English: [summary.en.md](summary.en.md)*

このファイルは `uv run agenteval verify` の生成物。手で編集しない。

| 判定 | 件数 |
|---|---|
| PASS | 15 |
| NEGATIVE | 6 |
| FAIL | 8 |
| PENDING | 0 |

| 実験 | 章 | タイトル | 判定 | 主要指標 | 来歴 | コスト(USD) |
|---|---|---|---|---|---|---|
| [E3-1](E3-1.md) | 3 | 軌跡カバレッジ依存グラフによる候補絞り込み | PASS | recall_of_flipped=1○ / candidate_ratio=0.348○ / control_false_candidates=0○ | simulated | 0.0 |
| [E3-2](E3-2.md) | 3 | 意味的影響推定による選択 | PASS | recall_of_flipped=1○ / control_selected_ratio=0○ / precision_gain_vs_coverage=0.0893○ | simulated | 0.0 |
| [E3-3](E3-3.md) | 3 | 不確実性駆動のテスト選択 | NEGATIVE | escape_ratio_vs_random=1.06× / calibration_error=0.08○ | simulated | 0.0 |
| [E3-4](E3-4.md) | 3 | テストピラミッドの段判定 | PASS | level_ok_rate=1○ / control_nonflake_flips=0○ | simulated | 0.0 |
| [E3-5](E3-5.md) | 3 | 接頭辞キャッシュと分岐再実行 | NEGATIVE | outcome_agreement=1○ / live_step_ratio_v02=1× / divergence_order_ok=0× / self_branch_live_steps=0○ | simulated | 0.0 |
| [E3-6](E3-6.md) | 3 | SPRT による早期打切り | PASS | empirical_alpha=0.0455○ / empirical_beta=0.0516○ / mean_n_ratio=0.544○ / boundary_mean_n=5.2○ | simulated | 0.0 |
| [E3-7](E3-7.md) | 3 | 逃走欠陥率・ε-探索・不可侵集合 | NEGATIVE | escape_rate=0.479× / random_included=2○ / inviolable_rate=1○ / full_budget_escape=0○ | simulated | 0.0 |
| [E4-1](E4-1.md) | 4 | 軌跡メトリクスの選択的反応 | PASS | dup_ratio_v04_vs_v01=44○ / verification_ratio_v03_vs_v01=0○ / v02_change_within_flake_band=1○ | simulated | 0.0 |
| [E4-2](E4-2.md) | 4 | アブレーションによる無駄呼び出し判定 | FAIL | waste_rate_diff=0.0241× / surrogate_precision=0.16× / surrogate_recall=1○ / control_useful_rate=1○ | simulated | 0.0 |
| [E4-3](E4-3.md) | 4 | 軌跡リンターの決定的検出 | PASS | violation_detection_rate=1○ / baseline_violation_rate=0○ / operator_tests_pass=1○ | simulated | 0.0 |
| [E4-4](E4-4.md) | 4 | 半順序マイルストーンの部分点 | FAIL | spearman_rho=-0.272× / synthetic_ok=1○ | simulated | 0.0 |
| [E4-5](E4-5.md) | 4 | ステップ単位判定（ルーブリックと参照方策一致） | PASS | mutation_drop_rate_rubric=0.967○ / mutation_drop_rate_refpolicy=1○ / original_mean_score=4.87○ / rejudge_agreement=1○ | simulated | 0.0 |
| [E4-6](E4-6.md) | 4 | 計画の外在化と乖離率 | FAIL | plan_valid_rate_v01p=1○ / delta_gap=0.197× / deviation_judge_rate=0.978○ | simulated | 0.0 |
| [E4-7](E4-7.md) | 4 | 軌跡内 NIAH（想起率とコンテキスト戦略） | FAIL | recall_n0=1○ / recall_drop_v01=0.667○ / auc_gain_v08=0× | simulated | 0.0 |
| [E4-8](E4-8.md) | 4 | pass@k / pass^k と一貫性 | PASS | boundary_gap=0.749○ / trivial_gap=0○ / var_steps_v04_gt_v01=1○ | simulated | 0.0 |
| [E4-9](E4-9.md) | 4 | コスト予算と Goodhart ペア指標 | PASS | steps_ratio_v07=0.796○ / verification_ratio_v07=0○ / p95_exceed_v04=1○ / p95_exceed_v01=0○ | simulated | 0.0 |
| [E8-1](E8-1.md) | 8 | 模擬忠実度と TTL | PASS | fidelity_after=0○ / fidelity_before=1○ / mismatch_keys_match=1○ / ttl_detection_rate=1○ | simulated | 0.0 |
| [E8-2](E8-2.md) | 8 | 鮮度と分布距離 | PASS | js_monotonic=1○ / new_category_alarm=1○ / mmd_p_value=0.0166○ / control_p_ge_005_rate=0.95○ | simulated | 0.0 |
| [E8-3](E8-3.md) | 8 | 弁別力と飽和 | NEGATIVE | trivial_abs_d=0○ / trivial_saturated=1○ / boundary_d=0.0677× / lifecycle_received=1○ | simulated | 0.0 |
| [E8-4](E8-4.md) | 8 | 二重トラック κ | PASS | kappa_before=1○ / kappa_drop=1○ | simulated | 0.0 |
| [E8-5](E8-5.md) | 8 | ドリフト帰属（要因別入替） | PASS | single_factor_match_rate=1○ / two_factor_top2_match=1○ / control_alarm=0○ | simulated | 0.0 |
| [E8-6](E8-6.md) | 8 | モデル指紋による入替検知 | PASS | control_false_alarm_rate=0○ / swap_p_value=0.002○ / live_control_p_value=0.716○ / live_swap_p_value=0.0455○ | simulated | 0.0 |
| [E8-7](E8-7.md) | 8 | 本番→テスト・パイプライン | FAIL | stratified_lift=1.89× / approval_rate=1○ / js_after_lt_before=1○ | simulated | 0.0 |
| [E8-8](E8-8.md) | 8 | テストのライフサイクル状態機械 | PASS | transitions_pass=1○ / retired_terminal=1○ / append_only=1○ / no_trigger_stays=1○ | unit | 0.0 |
| [E8-9](E8-9.md) | 8 | 汚染検知（カナリア走査） | FAIL | canary_detection_rate=1○ / baseline_hits=0○ / gap_v10=0.177× / gap_v01=-0.0476○ | simulated | 0.0 |
| [E0-1](E0-1.md) | 0 | シミュレート・エージェントと live の一致度（sim の妥当性） | FAIL | outcome_agreement=0.817○ / pass_rate_gap=0.183× / tool_sequence_similarity=0.668○ / version_ordering_preserved=0× / n_separable_version_pairs=2○ / ordering_ok_on_separable_pairs=1○ | live | 1.6793 |
| [E0-2](E0-2.md) | 0 | ジャッジの一致度と安定性（live Sonnet 5 と決定的代替判定器） | NEGATIVE | score_agreement_within_1=0.683× / mutation_drop_rate_live=0.989○ / rejudge_agreement_live=1○ / original_mean_score_live=3.7○ | live | 0.6382 |
| [E3-8](E3-8.md) | 3 | 学習済み故障予測によるテスト選択 | FAIL | roc_auc=0.826○ / recall_at_budget_30=0.535× / recall_ratio_vs_random=1.16× / escape_ratio_vs_random=0.868× / control_random_recall=0.463○ | simulated | 0.0 |
| [E3-9](E3-9.md) | 3 | 不確実性の役割は探索である（校正の維持） | NEGATIVE | calibration_gain=-0.0023× / escape_gain=0.0096○ / exploration_cost=0.0487○ | simulated | 0.0 |

総コスト: 2.3175 USD
