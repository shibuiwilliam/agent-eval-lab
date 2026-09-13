# 未解決の疑問・仮定

セッション中に生じた疑問と、その場で置いた仮定を追記する。解決したら「→ 解決: ...」を付けて残す（消さない）。

- [ ] Haiku 4.5 で `strict: true` のツール定義が受け付けられるか（P0 で確認し、結果を `llm/models.py` のコメントに残す）
- [ ] `effort` パラメータの正確な API 形式（P3 のジャッジ実装前に公式 docs で確認）
- [ ] ベースライン v01 の合格率が 50〜80% に入るタスク難度（P1 で実測して調整）
- [x] Haiku 4.5 で `strict: true` のツール定義が受け付けられるか → 未解決のまま。live 実行ができないため確認できず。`llm/models.py` の `USE_STRICT_TOOLS = False` を既定にし、引数検証は pydantic 側で行い、失敗は `tool_result.is_error` で返す実装にした
- [x] `effort` パラメータの正確な API 形式 → 未解決のまま。`judge_request_defaults()` は `max_tokens` だけを返し、`effort` は付けない（400 を避けるため）。live を使う前に公式 docs で確認が要る
- [x] ベースライン v01 の合格率が 50〜80% に入るタスク難度 → sim モードで 0.79。`Task.sim.difficulty` を 0.3（削除系は 0.2）にし、誤りの種類を行動ごとに acceptance へ効くものにして調整した
- [ ] live で同じ結論が出るか。特に (1) 分岐再実行の節約（E3-5 は sim で NEGATIVE）、(2) ジャッジの再判定の安定性（E4-5 は代替判定器なので 1.0 に固定される）、(3) モデル指紋（E8-6 は人工分布）
- [ ] `.claude/rules/process.md` は規則ファイルを `rules/global.py` と書いているが、`global` は Python の予約語で import できないため `rules/global_rules.py` にした。規則側の表記を直すか、実装に合わせるか
- [ ] 冗長性ペナルティ（`.claude/rules/pts.md`）の類似度をツール名列だけで測ると、同種タスクが相互に重複と判定されて選択が壊れる（E3-3 / E3-7 の FAIL の原因）。正規化引数を含める案を検討するか、タスクのツール列を多様にするか
