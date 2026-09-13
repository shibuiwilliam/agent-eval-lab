---
paths:
  - "src/agenteval/llm/**"
  - "pricing.yaml"
  - "corpus.yaml"
---

# LLM クライアントとコスト管理の規則

## LLMClient（`src/agenteval/llm/client.py`）
- 公開 API は `create(req: MessageRequest) -> MessageResult` の1つだけ。`MessageRequest` は pydantic で `model, system, messages, tools, tool_choice, max_tokens, effort` を持つ
- 内部で `anthropic.Anthropic(max_retries=4, timeout=120)` を使う。並列実行は `AsyncAnthropic` ＋ `asyncio.Semaphore(4)`。レート制限は SDK のリトライに任せ、それでも失敗したら `RateLimited` を投げて呼び出し元で待つ
- サンプリング引数（`temperature` 等）を受け付けない。`MessageRequest` にフィールドを作らない
- `metadata` やリクエスト ID はハッシュ対象に含めない

## 記録／再生カセット
- キー: `sha256(json.dumps(req.model_dump(), sort_keys=True, ensure_ascii=False, separators=(",", ":")))`
- 保存先: `data/cassettes/llm/<model>/<hash[:2]>/<hash>.json`。内容は `{request, response, usage, recorded_at, sdk_version}`。`response` は SDK の `Message.model_dump()`
- モード（`AGENTEVAL_LLM_MODE`）: `replay`（miss → `CassetteMiss` 例外）、`record`（常に live、上書き）、`auto`（hit → replay、miss → live ＋ 保存）。CLI 既定は `auto`、pytest は `replay` 固定
- `CassetteMiss` は分岐再実行（`pts/prefix_cache.py`）では「旧軌跡から分岐した」という信号として扱う。握りつぶさない
- 単体テスト用の小さなカセットは `data/fixtures/cassettes/` に置きコミットする。`data/cassettes/` はコミットしない

## コスト計測
- `response.usage` の `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` を必ず記録する
- 単価は `pricing.yaml` から読む。形式:
  ```yaml
  verified_at: "2026-09-13"
  source: "https://platform.claude.com/docs/en/about-claude/pricing"
  models:
    claude-haiku-4-5-20251001: {input: 1.00, output: 5.00, cache_write_5m: 1.25, cache_read: 0.10}
    claude-sonnet-5:           {input: 2.00, output: 10.00, cache_write_5m: 2.50, cache_read: 0.20}
  ```
  単位は USD / MTok。改定があり得るので、値は公式ページで確認した日付を更新してから使う
- 台帳 `data/cost_ledger.jsonl` に1呼び出し1行（run_id, step, model, tokens, usd, mode）。replay の行は usd=0 で記録する
- `BudgetGuard`: 呼び出し前に「累積 ＋ 直近平均コスト」が `AGENTEVAL_BUDGET_USD` を超えるなら `BudgetExceeded` を投げる。実験スクリプトはこれを捕まえて結果ページに「予算停止」と書く

## プロンプトキャッシュ
- system ブロックとツール定義の末尾に `cache_control: {"type": "ephemeral"}` を付ける。エージェントループでは会話接頭辞が毎ステップ同じなので効く
- 効いているかは `cache_read_input_tokens` で確認し、`agenteval cost` に cache hit 率を出す

## ツール定義と構造化出力
- ツール定義は `env/tools.py` の pydantic モデルから JSON Schema を生成する。手書きの schema を持たない
- 対応モデルでは `strict: true` を付けて schema 準拠を保証する。未対応または 400 が返る場合は pydantic で検証し、エラーは `tool_result` の `is_error: true` として返す（エージェントの復帰行動の観察対象になる）
- ジャッジは `submit_verdict` ツール（`score`, `reason`, `evidence_refs`）を定義し `tool_choice: {"type": "tool", "name": "submit_verdict"}` で強制する。Sonnet 5 は適応思考のまま強制ツール使用に対応している
- `effort` はジャッジ側で下げてコストを抑えてよい。パラメータの正確な形式は公式 docs（build-with-claude/effort）を確認し `models.py` の `judge_request_defaults()` に集約する

## 見積り（`--dry-run`）
- 呼び出し回数 × 平均トークン（同じタスク種別の過去 run の平均、無ければ `corpus.yaml` の既定値）× 単価。過去 run が無い初回は保守的に 1.5 倍して出す
- Batch API（50% 割引）はステップ判定など**独立した呼び出しの束**にだけ検討する。エージェントループには使わない（逐次依存があり複雑化に見合わない）
