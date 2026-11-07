# 未解決の疑問・仮定

セッション中に生じた疑問と、その場で置いた仮定を追記する。解決したら「→ 解決: ...」を付けて残す（消さない）。

- [ ] Haiku 4.5 で `strict: true` のツール定義が受け付けられるか（P0 で確認し、結果を `llm/models.py` のコメントに残す）
- [ ] `effort` パラメータの正確な API 形式（P3 のジャッジ実装前に公式 docs で確認）
- [ ] ベースライン v01 の合格率が 50〜80% に入るタスク難度（P1 で実測して調整）
- [x] Haiku 4.5 で `strict: true` のツール定義が受け付けられるか → **解決（live で確認、2026-09-13）**。受け付けられるが (1) 入れ子の object すべてに `additionalProperties: false` が必要、(2) 13 ツール同時では `400 Schema is too complex.`、(3) strict は v06 の植込み（誤った引数名からの復帰）を API 側で抑止して観測対象を消す。以上より `USE_STRICT_TOOLS = False` を既定のまま維持し、根拠を `llm/models.py` に記録した
- [x] `effort` パラメータの正確な API 形式 → **解決（live で確認、2026-09-13）**。`output_config: {"effort": "low"}`。Sonnet 5 は対応、**Haiku 4.5 は 400**（`This model does not support the effort parameter.`）。`judge_request_defaults()` に追加し、`supports_effort(model)` を用意した
- [x] ベースライン v01 の合格率が 50〜80% に入るタスク難度 → sim モードで 0.79。`Task.sim.difficulty` を 0.3（削除系は 0.2）にし、誤りの種類を行動ごとに acceptance へ効くものにして調整した
- [x] sim は live の代替になるか → **部分的に成立（E0-1 / docs/results/LIVE.md）**。合否一致率 0.82、ツール列類似度 0.66。ただし `stop_appropriate` は sim 1.0 / live 0.66 で一致しない。`finish` 依存の指標を sim で語ってはいけない
- [ ] live で同じ結論が出るか。残り: (1) 分岐再実行の節約（E3-5 は sim で NEGATIVE）、(2) ジャッジの再判定の安定性（E4-5 は代替判定器なので 1.0 に固定される）、(3) モデル指紋（E8-6 は人工分布）
- [ ] E0-1 の `version_ordering_preserved` は、版の合格率が信頼区間内で同点のとき定義できない。基準の書き方を「順序の一致」から「差が信頼区間に収まるか」に変えるべきか（ADR が要る）
- [ ] `.claude/rules/process.md` は規則ファイルを `rules/global.py` と書いているが、`global` は Python の予約語で import できないため `rules/global_rules.py` にした。規則側の表記を直すか、実装に合わせるか
- [ ] 冗長性ペナルティ（`.claude/rules/pts.md`）の類似度をツール名列だけで測ると、同種タスクが相互に重複と判定されて選択が壊れる（E3-3 / E3-7 の FAIL の原因）。正規化引数を含める案を検討するか、タスクのツール列を多様にするか
- [ ] （2026-11-07、W2-V5）`tests/env/test_sandbox.py` の `test_docker_background_children_are_ended_with_the_call[setsid ...]` が、全テストを並行負荷の中で流したときに 1 件だけ落ち、単独の再実行では 15/15 通った。`elapsed < 1.5` 秒の壁時計の閾値が負荷で外れたと見られる（背景の子を呼び出しの終了時に終わらせる機能の欠陥ではなく、時間の閾値の揺れ）。閾値を緩めるか、壁時計ではなく「呼び出しが背景の子の終了を待たずに戻ったこと」を別の観測（子の PID の生存、パイプの EOF）で確かめる形に変えるか。`make check` は通っているが、負荷の高い CI では偽の失敗が出うる
- [ ] （2026-11-07、TASK.md の W1-I5 の申し送りから移した）孤立サロゲートを含むツール入力が live の応答に混ざると、`canonical_json`・`sha256_of`・`cassette_key`・合成トークン数・Run の JSON 保存で UnicodeEncodeError になりうる（共有コードに及ぶ）。第 1 段のオフライン検証では起きていない。live（W3・W4）で発生したら runner が報告し、どの層で置き換える（エスケープする）かを決める
- [ ] （2026-11-07、TASK.md の W1-I5 の申し送りから移した）`search_code` の正規表現は別プロセスで壁時計 5 秒で打ち切るので、負荷によって打ち切られるかどうか（＝ツール結果）が変わり、replay でも完全には再現しない。打ち切りの有無を trace に残して replay で照合するか、時間ではなく操作数で打ち切るか
- [ ] （2026-11-07、W2-A1）許可リストの `python -m pip install -e . --no-deps --no-build-isolation`（CR-P1）が Docker 上で偽 HOME に書くか（書けば E1 の誤警報）。CONTENT.md 10.5 節は W2-V4 で確かめるとしていたが、オフラインのスクリプト化 run 196 本はどれも pip を呼んでおらず未確認。live（G1 のパイロット）の前に 1 回だけ確かめたい
- [ ] （2026-11-07、W2-A1）実験スクリプトの結果ページの引き継ぎ（`experiments/_coding_common.py` の `carry_over_designer_page`・`design_method`）は見出しの完全一致（`## 5項目`・`## 方法`）しか探さないので、EC-S1 では設計者の先頭ブロック・5 項目・方法が生成時に落ちた（W2-A1 で戻し、見出しを揃えた）。また「## 判定」の節は引き継がれず、再実行で設計者の記述が消える。見出しの照合を前方一致にするか、判定の解釈を「気づき・限界」に置く運用にするか
- [ ] （2026-11-07、W3-A2）EC-B2 の H2（`verbose_attribution_to_run_tests`）は `sub10` × 1 では検出力が足りない（予測コスト増 約 0.04 USD < 必要 0.101 USD）。オフラインで詳細出力の実際の大きさを測り（費用 0）、G2 で「c04 を `core24` × 3 に広げる（約 +3.7 USD）」か「H2 を記録のみにする」かを ADR で決める（`docs/results/EC-B2.md` の W3-A2 の計算）
- [ ] （2026-11-07、W3-A2）EC-R2 の `silent_wrong_gain_c13_ci_low > 0` は c13 の F の率で到達上限が決まる（$w(f + f^2 + f^3)$）。c13 の f̂ < 0.15 での不合格は NEGATIVE ではなく検出力不足と書くと EC-R2.md で事前に固定した。c13 の立て直し（範囲の制限）が F を増やすか減らすかは見ていない
- [ ] （2026-11-07、W3-A2）CONTENT.md 4.2 節の難度のつまみ（距離・囮・隠しテストの本数）は Haiku 4.5 の合格率をほとんど動かさなかった（L1 0.958、L3 0.875）。第 2 段で難度の幅が要るなら、変異の目立ちにくさ（複数トークン、docstring と矛盾しない誤り）をつまみにして作り直す（ADR-043 判断 4）
