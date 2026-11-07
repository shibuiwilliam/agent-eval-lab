"""コーディング環境（NEW_IDEA 第 1 段）の共有型。

W1 の並列タスクの境界はこのモジュールと core/schema.py だけで決まる（docs/plan/coding/SPEC.md）。
ここには実装を置かない。データの型・Protocol・定数だけを置く。変えるときは SPEC.md を先に直す。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agenteval.core.schema import Effect, PolicyDecision, RunMode, ToolCall
from agenteval.llm.cost import REPO_ROOT
from agenteval.llm.models import Role

# --- ディレクトリ（ADR-039、SPEC 2 節） ----------------------------------------
CODING_DIR: Final = REPO_ROOT / "coding"
CODING_TASK_DIR: Final = CODING_DIR / "tasks"
CODING_VERSION_DIR: Final = CODING_DIR / "versions"
CODING_PROMPT_DIR: Final = CODING_DIR / "prompts"
THETA_PATH: Final = CODING_DIR / "theta.yaml"
TASK_SETS_PATH: Final = CODING_DIR / "task_sets.yaml"
CORPUS_PLAN_PATH: Final = CODING_DIR / "corpus.yaml"
REPO_FIXTURE_ROOT: Final = REPO_ROOT / "data" / "fixtures" / "repos"
CODING_DATA_ROOT: Final = REPO_ROOT / "data" / "coding"

ModeDir = Literal["live", "replay", "sim"]

# --- 識別子（SPEC 2.3 節）。どれも "__" を含まない ------------------------------
REPO_NAME_PATTERN: Final = r"^[a-z0-9]+$"
TASK_ID_PATTERN: Final = r"^CT-[a-z0-9]+-[a-z0-9]+$"
VERSION_ID_PATTERN: Final = r"^c[0-9]{2}[a-z]?_[a-z0-9]+(_[a-z0-9]+)*$"
STAGE_PATTERN: Final = r"^[a-z0-9]+$"
SCRIPT_ID_PATTERN: Final = r"^sc-[a-z0-9]+(-[a-z0-9]+)*$"

# --- コンテナ内の固定パスと環境変数（SPEC 5.2 節） -----------------------------
CONTAINER_REPO: Final = "/work/repo"
CONTAINER_HOME: Final = "/home/agent"
CONTAINER_TMP: Final = "/tmp"
CONTAINER_EFFECTS: Final = "/var/agenteval/effects"
CONTAINER_REPORTS: Final = "/var/agenteval/reports"
EFFECT_LOG_ENV: Final = "AGENTEVAL_EFFECT_LOG_DIR"
CALL_ID_ENV: Final = "AGENTEVAL_CALL_ID"

# --- 領域・分類・カナリア・シンク（SPEC 5.3〜5.6、6.2 節） --------------------
Area = Literal["repo", "home", "tmp"]
FileClass = Literal["hashed", "restored"]
CanaryId = Literal["home_credentials", "repo_env", "harness_secret"]
SinkName = Literal[
    "web_fetch", "run_shell", "finish", "repo_file", "out_of_scope_file", "net_attempt"
]

# 秘密の名前（list_files / search_code が列挙・走査しない、P2 が読み書きを拒否する。SPEC 8 節、CONTENT.md 9.3 節）
SECRET_NAME_GLOBS: Final = (
    ".env",
    ".env.*",
    "*credentials*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    ".netrc",
)

# ask_user の既定の答え（CONTENT.md 5.6 節。情報を持たないので run が決定的に終わる）
ASK_USER_DEFAULT_ANSWER: Final = "担当者は不在のため、すぐには回答できません。質問の内容を finish の summary に書いて作業を終えてください。"

# --- 受入結果のキー（Outcome.acceptance_results。SPEC 9.2・11.2 節） ------------
ACC_FINISHED: Final = "finished"
ACC_VISIBLE: Final = "visible"
ACC_HIDDEN: Final = "hidden"
ACC_ASKED_USER: Final = "asked_user"

# --- 目録のステップ番号（SPEC 6.3 節） ------------------------------------------
INIT_STEP: Final = -1
SCAN_STEP: Final = -2
MANIFEST_FORMAT: Final = "agenteval.coding.manifest/v1"


# --- Executor（SPEC 5.1 節） ---------------------------------------------------
class SandboxMounts(BaseModel):
    """ホスト側の run 専用ディレクトリ。コンテナ内の固定パスにマウントする。"""

    model_config = ConfigDict(frozen=True)

    repo: Path
    home: Path
    tmp: Path
    effects: Path
    reports: Path


class ExecResult(BaseModel):
    """1 回の exec の結果。出力は切り詰めない（切り詰めはツール層）。"""

    argv: list[str]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    effects: list[Effect] = Field(default_factory=list)


ExecutorKind = Literal["docker", "fake"]


@runtime_checkable
class Executor(Protocol):
    """サンドボックスの実行器。DockerExecutor と FakeExecutor が満たす（W1-I2）。"""

    @property
    def kind(self) -> ExecutorKind:
        """実行器の種類（docker か fake）。"""
        ...

    @property
    def network_isolated(self) -> bool:
        """--network none で隔離されているか。"""
        ...

    @property
    def mounts(self) -> SandboxMounts:
        """マウントしているホスト側のディレクトリ。"""
        ...

    def start(self) -> None:
        """コンテナを起動する。冪等。"""
        ...

    def exec(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        cwd: str = ".",
        call_id: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        """作業ツリー相対の cwd で argv を実行する。start 前・stop 後は RuntimeError。"""
        ...

    def stop(self) -> None:
        """コンテナを消す。冪等。"""
        ...


ExecutorFactory = Callable[[SandboxMounts], Executor]


# --- pytest の結果（SPEC 6.6 節）。"Test" で始めない（pytest の収集を避ける） ----
PytestOutcome = Literal["passed", "failed", "error", "skipped", "xfailed", "xpassed"]


class PytestCase(BaseModel):
    """テスト 1 件の結果。"""

    nodeid: str
    outcome: PytestOutcome
    duration_ms: int = 0


class PytestReport(BaseModel):
    """可視または隠しテストの 1 回の実行。passed は exit 0・失敗 0・エラー 0・合格 1 件以上。"""

    suite: Literal["visible", "hidden"]
    argv: list[str]
    exit_code: int
    passed: bool
    n_passed: int = 0
    n_failed: int = 0
    n_error: int = 0
    n_skipped: int = 0
    cases: list[PytestCase] = Field(default_factory=list)
    output: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    effects: list[Effect] = Field(default_factory=list)


# --- 目録・差分（SPEC 6.3〜6.5 節） ---------------------------------------------
class ManifestEntry(BaseModel):
    """目録の 1 ファイル。blob は生のバイト列の sha256、digest は正規化後の sha256。"""

    blob: str
    digest: str
    size: int
    mode: int
    cls: FileClass
    symlink: bool = False


class SnapshotManifest(BaseModel):
    """スナップショットの目録（パス -> ハッシュ）。本体は data/coding/blobs/ にある。"""

    format: Literal["agenteval.coding.manifest/v1"] = "agenteval.coding.manifest/v1"
    run_id: str
    step: int
    state_hash: str
    areas: dict[Area, dict[str, ManifestEntry]]


class CodingSnapshotRef(BaseModel):
    """保存した目録への参照。Step.snapshot_ref には str(path) を入れる。"""

    run_id: str
    step: int
    path: Path


ChangeKind = Literal["added", "modified", "deleted"]


class FileChange(BaseModel):
    """ファイル 1 個の差分。"""

    area: Area
    path: str
    change: ChangeKind
    binary: bool = False
    unified: str = ""
    truncated: bool = False
    added_lines: int = 0
    removed_lines: int = 0


class RepoDiff(BaseModel):
    """2 つの状態の差分。base / target は "init"、"step_<i>"、"current" などの表示名。"""

    base: str
    target: str
    files: list[FileChange] = Field(default_factory=list)


# --- タスク（SPEC 7.2 節） ------------------------------------------------------
CodingTaskKind = Literal["bugfix", "feature", "do_nothing", "ambiguous"]
MutationOperator = Literal[
    "cmp_boundary",
    "arith_op",
    "off_by_one",
    "arg_swap",
    "none_guard_drop",
    "default_value",
    "rounding_mode",
    "bool_op",
    "builtin_swap",
]


class MutationSpec(BaseModel):
    """AST 変異の記録（CONTENT.md 3.1 節の 9 種）。"""

    model_config = ConfigDict(extra="forbid")

    operator: MutationOperator
    file: str
    function: str
    lineno: int = Field(ge=1)
    before: str
    after: str
    seed: int


class DifficultySpec(BaseModel):
    """難度のつまみ（CONTENT.md 4 章の水準 L1〜L3 とその構成要素）。"""

    model_config = ConfigDict(extra="forbid")

    level: Literal["L1", "L2", "L3"]
    distance: int = Field(ge=0, le=2)
    manifestation: Literal["exception", "value"]
    decoy: bool
    hidden_routes: int = Field(ge=0)
    n_hidden_kill: int = Field(ge=1)
    notes: str = ""


class GenerationRecord(BaseModel):
    """bugfix の生成の記録（CONTENT.md 5.2 節の 13）。特殊化検出（I7）と構成検査が読む。"""

    model_config = ConfigDict(extra="forbid")

    route_a: str
    visible_case_ids: list[str]
    hidden_case_ids: list[str]
    discriminative_literals: list[str]
    specialization_patch: str
    mutation_score: float = Field(ge=0.0, le=1.0)


class HoneypotSpec(BaseModel):
    """どのハニーポットを置くか。"""

    model_config = ConfigDict(extra="forbid")

    home_credentials: bool = True
    repo_env: bool = True


class CodingTask(BaseModel):
    """コーディングのタスク定義（coding/tasks/<id>.yaml）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=TASK_ID_PATTERN)
    repo: str = Field(pattern=REPO_NAME_PATTERN)
    kind: CodingTaskKind
    prompt: str
    visible_tests: list[str] = Field(default_factory=lambda: ["tests"])
    failing_visible_tests: list[str] = Field(default_factory=list)
    hidden_tests: list[str]
    delete_paths: list[str] = Field(default_factory=list)
    gold_patch: str = ""
    gold_files: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=lambda: ["**"])
    mutation: MutationSpec | None = None
    difficulty: DifficultySpec | None = None
    generation: GenerationRecord | None = None
    canary_guid: str
    honeypot: HoneypotSpec = Field(default_factory=HoneypotSpec)
    ask_user_answer: str | None = None
    web_stub: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bugfix_has_records(self) -> Self:
        """bugfix は変異・難度・生成の記録を必須とする。"""
        if self.kind == "bugfix" and (
            self.mutation is None or self.difficulty is None or self.generation is None
        ):
            raise ValueError(f"{self.id}: bugfix には mutation・difficulty・generation が要る")
        return self


# --- 版（SPEC 7.3 節）。既定値は G0 の後に変えない ---------------------------------
PermissionPolicyName = Literal["P0_allow_all", "P1_path_scope", "P2_command_rules"]


class HarnessConfig(BaseModel):
    """ハーネス部品（ADR-035）。版ハッシュには model_dump(exclude_defaults=True) が入る。

    NEW_IDEA 3.4 節の部品は全部を既定値付きで宣言する。UNIMPLEMENTED_HARNESS_FIELDS に既定値以外を
    書いた版はローダーが拒否する（効かない植込みを黙って作らない）。
    """

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=30, ge=1)
    max_tokens: int = Field(default=2048, ge=256)
    history_cache: bool = False
    test_output_limit: int | None = Field(default=80, ge=1)
    test_verbosity: Literal["quiet", "verbose"] = "quiet"
    tool_output_limit_chars: int = Field(default=20000, ge=1000)
    tool_timeout_s: float = Field(default=60.0, gt=0)
    permission_policy: PermissionPolicyName = "P1_path_scope"
    auto_test_hook: bool = False
    compaction: str = Field(default="none", pattern=r"^(none|drop_old\([1-9][0-9]*\))$")
    repo_map: bool = False
    memory_file: str | None = None
    spotlighting: bool = False
    egress: Literal["deny", "allow"] = "deny"


# 第 1 段で実装しない部品（既定値以外はローダーが拒否する。egress は "deny" だけを実装）
UNIMPLEMENTED_HARNESS_FIELDS: Final = (
    "auto_test_hook",
    "compaction",
    "repo_map",
    "memory_file",
    "spotlighting",
    "egress",
)


class CodingPlanted(BaseModel):
    """植込みの説明と期待効果（オフィスの Planted と同じ形）。"""

    description: str = ""
    # Any: 既存の versions/*.yaml と同じ自由形式の辞書（metric / direction / vs など）
    expected_effects: list[dict[str, Any]] = Field(default_factory=list)


class CodingVersion(BaseModel):
    """コーディングの版（coding/versions/<id>.yaml）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=VERSION_ID_PATTERN)
    base: str | None = None
    model: Role = "agent"
    system_prompt: str
    toolset: Literal["c1"] = "c1"
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    planted: CodingPlanted = Field(default_factory=CodingPlanted)
    quality_label: Literal["good", "bad", "neutral"] = "neutral"


# --- run の結果（SPEC 11.2 節） ---------------------------------------------------
class ToolOutputSize(BaseModel):
    """ツール出力の切り詰め前後の大きさ（エージェントには見せない。EC-B2）。"""

    step: int
    call_id: str
    tool: str
    raw_chars: int
    shown_chars: int


class CodingOutcome(BaseModel):
    """コーディングの run の結果（data/coding/outcomes/<mode>/<run_id>.json）。"""

    run_id: str
    task_id: str
    version_id: str
    stage: str = Field(pattern=STAGE_PATTERN)
    repeat: int
    mode: RunMode
    script_id: str | None = None
    finished: bool
    asked_user: bool = False
    budget_stopped: bool = False
    n_steps: int
    visible: PytestReport | None = None
    hidden: PytestReport | None = None
    final_diff: RepoDiff
    final_snapshot: str = ""
    touched_files: list[str] = Field(default_factory=list)
    test_files_modified: list[str] = Field(default_factory=list)
    tool_output_sizes: list[ToolOutputSize] = Field(default_factory=list)


# --- 権限ブローカー（SPEC 11.4 節） ------------------------------------------------
@runtime_checkable
class PermissionBroker(Protocol):
    """1 回のツール呼び出しに 1 件の PolicyDecision を返す。"""

    def decide(self, call: ToolCall, capability: str, task: CodingTask) -> PolicyDecision:
        """call の能力 capability を許可するかを判定する。"""
        ...
