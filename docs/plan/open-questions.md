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
