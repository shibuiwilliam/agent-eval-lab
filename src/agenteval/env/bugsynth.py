"""自作リポジトリの AST 変異による bugfix タスク生成（W1-I4。CONTENT.md 3〜5・12 章、SPEC.md 7 節）。

処理の流れ:

1. ケースバンクを作る（`build_casebank`）。リポジトリごとの `casegen.py` が入力（呼び出し式）を並べ、
   正しいコードで期待値を計算して `casebank.json` に凍結する（CONTENT.md 1.3 節）。
2. 正しいコードに 9 種の AST 変異オペレータを当て、候補を列挙する（`enumerate_candidates`）。
   候補ごとに変異コードでケースバンクを走らせ、殺す集合 K を得る（`evaluate_candidates`）。
3. 等価変異・不適な変異を除き、難度水準 L1〜L3 に分類し、seed 固定の乱数でタスクを選ぶ（`generate`）。
4. 可視テスト・隠しテスト・オーバーレイ・正解パッチ・特殊化パッチ・課題文・YAML を書き出す。
5. ホストの pytest で構成検査 C1〜C13 を確かめる（`verify_all`。自作の無害なコードなのでホストで走らせてよい）。

外部依存は標準ライブラリと pydantic（`CodingTask`）、PyYAML、numpy（乱数）だけである。
変異コードの実行は、標準ライブラリだけの小さな runner を別プロセスで走らせて行う（時間切れで落ちても親を巻き込まない）。
"""

from __future__ import annotations

import argparse
import ast
import bisect
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, Literal, cast, get_args

import numpy as np
import yaml

from agenteval.core.coding_types import (
    REPO_FIXTURE_ROOT,
    TASK_SETS_PATH,
    CodingTask,
    DifficultySpec,
    GenerationRecord,
    MutationOperator,
    MutationSpec,
)
from agenteval.llm.cost import REPO_ROOT

# --- 定数 ---------------------------------------------------------------------
SELECTION_SEED: Final = 20261107
REPO_ORDER: Final = ("tillcalc", "datewright", "rowmill", "throttlebox", "binkeeper", "textmeter")
LEVELS: Final = ("L1", "L2", "L3")
LEVEL_QUOTA: Final = {"L1": 2, "L2": 3, "L3": 2}  # 1 リポジトリあたり（CONTENT.md 4.3 節）
LEVEL_KILL: Final = {"L1": 3, "L2": 3, "L3": 6}  # 隠しの殺すケース h（CONTENT.md 4.2 節）
LEVEL_CROSS: Final = {"L1": 0, "L2": 1, "L3": 2}  # 別経路の隠しケースの最小本数
LEVEL_DISTANCE: Final = {"L1": 0, "L2": 1, "L3": 2}
NOISY_RATIO: Final = 0.30  # 騒がしすぎる変異の閾値（CONTENT.md 3.2 節の 3）
CASE_TIMEOUT_S: Final = 5.0
PROCESS_TIMEOUT_S: Final = 180.0
OPERATORS: Final[tuple[str, ...]] = get_args(MutationOperator)
OPERATOR_MIN: Final = 3  # 全体で各オペレータ 3 件以上（O7 は 2 件以上）
OPERATOR_MAX: Final = 8
CODING_TASK_DIR: Final = REPO_ROOT / "coding" / "tasks"

TEXT_STOP_NUMBERS: Final = (0, 1, 2, 10, 100)  # 停止リストの数値（CONTENT.md 6.2 節の 4）

# runner が edge 集合を作るときの、ケースの種類
_JSON_INDENT: Final = 1


# --- runner（標準ライブラリだけ。変異コードを別プロセスで走らせる） -----------------
RUNNER_SOURCE: Final = r"""
import ast, dataclasses, datetime, decimal, enum, inspect, json, os, signal, sys

CASE_TIMEOUT = float(os.environ.get("BUGSYNTH_CASE_TIMEOUT", "5"))


class _Timeout(BaseException):
    pass


def _on_alarm(signum, frame):
    raise _Timeout()


def _namespace(meta):
    ns = {}
    exec("\n".join(meta["header"]) + "\nfrom " + meta["package"] + " import *\n", ns)
    return ns


def _expr(case):
    call = case["call"]
    view = case.get("view")
    if not view:
        return call
    bare = isinstance(ast.parse(call, mode="eval").body, ast.Call)
    return view.replace("$", call if bare else "(" + call + ")")


def _short(text, limit=300):
    return text if len(text) <= limit else text[:limit] + "..."


def _render(v, imports):
    # 値を Python の式（文字列）にする。できなければ None。
    if v is None or isinstance(v, (bool, int, str)):
        return repr(v)
    if isinstance(v, float):
        return repr(v) if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, decimal.Decimal):
        if not v.is_finite():
            return None
        imports.add(("decimal", "Decimal"))
        return "Decimal(" + repr(str(v)) + ")"
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        name = type(v).__name__
        imports.add(("datetime", name))
        if isinstance(v, datetime.datetime):
            parts = [v.year, v.month, v.day, v.hour, v.minute, v.second, v.microsecond]
        elif isinstance(v, datetime.date):
            parts = [v.year, v.month, v.day]
        else:
            parts = [v.hour, v.minute, v.second, v.microsecond]
        return name + "(" + ", ".join(str(p) for p in parts) + ")"
    if isinstance(v, enum.Enum):
        imports.add((type(v).__module__, type(v).__name__))
        return type(v).__name__ + "." + v.name
    if isinstance(v, (list, tuple)):
        items = [_render(x, imports) for x in v]
        if any(i is None for i in items):
            return None
        if isinstance(v, list):
            return "[" + ", ".join(items) + "]"
        return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"
    if isinstance(v, (set, frozenset)):
        items = sorted(_render(x, imports) or "" for x in v)
        if not v:
            return "set()"
        return "{" + ", ".join(items) + "}"
    if isinstance(v, dict):
        pairs = []
        for k, x in v.items():
            rk, rx = _render(k, imports), _render(x, imports)
            if rk is None or rx is None:
                return None
            pairs.append(rk + ": " + rx)
        return "{" + ", ".join(pairs) + "}"
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        parts = []
        for f in dataclasses.fields(v):
            if not f.init:
                continue
            rx = _render(getattr(v, f.name), imports)
            if rx is None:
                return None
            parts.append(f.name + "=" + rx)
        imports.add((type(v).__module__, type(v).__name__))
        return type(v).__name__ + "(" + ", ".join(parts) + ")"
    return None


def _run_one(case, ns, mode):
    signal.setitimer(signal.ITIMER_REAL, CASE_TIMEOUT)
    try:
        for stmt in case["setup"]:
            exec(stmt, ns)
        expr = _expr(case)
        if mode == "freeze":
            return _freeze(case, ns, expr)
        if case["raises"]:
            exc_cls = eval(case["raises"], ns)
            try:
                value = eval(case["call"], ns)
            except exc_cls:
                return {"s": "pass"}
            except Exception as exc:
                return {"s": "error", "a": type(exc).__name__ + ": " + _short(str(exc), 120)}
            return {"s": "fail", "a": "no exception: " + _short(repr(value), 120)}
        value = eval(expr, ns)
        if value == ast.literal_eval(case["expect"]):
            return {"s": "pass"}
        return {"s": "fail", "a": _short(repr(value))}
    except _Timeout:
        return {"s": "timeout"}
    except Exception as exc:
        return {"s": "error", "a": type(exc).__name__ + ": " + _short(str(exc), 120)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _freeze(case, ns, expr):
    if case["raises"]:
        try:
            eval(case["call"], ns)
        except Exception as exc:
            name = type(exc).__name__
            cls = eval(name, ns)
            if not (isinstance(cls, type) and isinstance(exc, cls)):
                return {"s": "error", "a": "例外クラス " + name + " を名前で引けない"}
            return {"s": "ok", "raises": name}
        return {"s": "error", "a": "例外が出なかった"}
    value = eval(expr, ns)
    text = repr(value)
    try:
        back = ast.literal_eval(text)
    except Exception:
        return {"s": "error", "a": "期待値がリテラルにならない: " + _short(text, 120)}
    if back != value:
        return {"s": "error", "a": "期待値が往復しない: " + _short(text, 120)}
    return {"s": "ok", "expect": text}


def _key(co, pkg_dir):
    fn = co.co_filename
    if not fn.startswith(pkg_dir + os.sep):
        return None
    qual = co.co_qualname.split(".<locals>")[0]
    if qual.startswith("<"):
        return None
    return os.path.splitext(os.path.basename(fn))[0] + "." + qual


def _trace_one(case, ns, pkg_dir):
    edges = set()
    stack = []

    def prof(frame, event, arg):
        if event == "call":
            key = _key(frame.f_code, pkg_dir)
            if key is not None:
                caller = next((k for k in reversed(stack) if k is not None), None)
                if caller is not None and caller != key:
                    edges.add((caller, key))
            stack.append(key)
        elif event == "return":
            if stack:
                stack.pop()

    signal.setitimer(signal.ITIMER_REAL, CASE_TIMEOUT)
    try:
        for stmt in case["setup"]:
            exec(stmt, ns)
        expr = _expr(case)
        sys.setprofile(prof)
        try:
            eval(expr, ns)
        finally:
            sys.setprofile(None)
        return {"s": "ok", "edges": sorted([a, b] for a, b in edges)}
    except _Timeout:
        return {"s": "timeout"}
    except Exception as exc:
        return {"s": "error", "a": type(exc).__name__ + ": " + _short(str(exc), 120)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _spec_one(case, ns):
    # 特殊化パッチの材料: 最後の呼び出しの引数・受け手の状態・結果を式にする
    signal.setitimer(signal.ITIMER_REAL, CASE_TIMEOUT)
    try:
        for stmt in case["setup"]:
            exec(stmt, ns)
        node = ast.parse(case["call"], mode="eval").body
        if not isinstance(node, ast.Call) or any(isinstance(a, ast.Starred) for a in node.args):
            return {"s": "no", "a": "呼び出し式でない"}
        if any(k.arg is None for k in node.keywords):
            return {"s": "no", "a": "** 展開がある"}
        fn = eval(ast.unparse(node.func), ns)
        args = [eval(ast.unparse(a), ns) for a in node.args]
        kwargs = {k.arg: eval(ast.unparse(k.value), ns) for k in node.keywords}
        sig = inspect.signature(fn)
        bound = sig.bind(*args, **kwargs)
        imports = set()
        params = {}
        for name, value in bound.arguments.items():
            params[name] = _render(value, imports)
        owner = getattr(fn, "__self__", None)
        self_expr, self_attrs = None, {}
        if owner is not None and not isinstance(owner, type):
            self_expr = _render(owner, imports)
            if self_expr is None and hasattr(owner, "__dict__"):
                for k, x in sorted(vars(owner).items()):
                    r = _render(x, imports)
                    if r is not None:
                        self_attrs[k] = r
        result = _render(fn(*args, **kwargs), imports)
        return {
            "s": "ok",
            "params": params,
            "self_expr": self_expr,
            "self_attrs": self_attrs,
            "result": result,
            "imports": sorted([m, n] for m, n in imports),
        }
    except _Timeout:
        return {"s": "timeout"}
    except Exception as exc:
        return {"s": "error", "a": type(exc).__name__ + ": " + _short(str(exc), 120)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main(argv):
    mode, src_dir, cases_path, out_path = argv[:4]
    src_dir = os.path.abspath(src_dir)
    sys.path.insert(0, src_dir)
    sys.setrecursionlimit(2000)
    signal.signal(signal.SIGALRM, _on_alarm)
    with open(cases_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    pkg_dir = os.path.join(src_dir, meta["package"])
    base_ns = _namespace(meta)
    out = {}
    for case in meta["cases"]:
        ns = dict(base_ns)
        if mode == "trace":
            out[case["id"]] = _trace_one(case, ns, pkg_dir)
        elif mode == "spec":
            out[case["id"]] = _spec_one(case, ns)
        else:
            out[case["id"]] = _run_one(case, ns, mode)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)


main(sys.argv[1:])
"""


# --- ケースバンク ----------------------------------------------------------------
@dataclass(frozen=True)
class Case:
    """ケースバンクの 1 ケース（公開関数の 1 回の呼び出しと期待値）。"""

    id: str
    target: str  # 公開関数の修飾名（定義元モジュール内。例: allocate、Money.times）
    module: str  # 定義元モジュールのファイル名（拡張子なし）
    setup: tuple[str, ...]
    call: str
    view: str | None
    expect: str | None  # repr 文字列（例外を期待するケースは None）
    raises: str | None  # 期待する例外クラス名
    tags: tuple[str, ...]

    @property
    def slug(self) -> str:
        """テスト関数名とケース ID に使う識別子（`Money.times` -> `money_times`）。"""
        return self.id.rsplit("-", 1)[0]

    @property
    def number(self) -> str:
        return self.id.rsplit("-", 1)[1]

    @property
    def test_name(self) -> str:
        return f"test_{self.slug}_{self.number}"

    @property
    def key(self) -> str:
        """target の関数キー（`module.qualname`）。"""
        return f"{self.module}.{self.target}"

    def to_json(self) -> dict[str, Any]:
        # Any: JSON に書き出す辞書（値は str / list / None）
        return {
            "id": self.id,
            "target": self.target,
            "module": self.module,
            "setup": list(self.setup),
            "call": self.call,
            "view": self.view,
            "expect": self.expect,
            "raises": self.raises,
            "tags": list(self.tags),
        }

    @staticmethod
    def from_json(d: Mapping[str, Any]) -> Case:
        return Case(
            id=d["id"],
            target=d["target"],
            module=d["module"],
            setup=tuple(d["setup"]),
            call=d["call"],
            view=d["view"],
            expect=d["expect"],
            raises=d["raises"],
            tags=tuple(d["tags"]),
        )

    def expression(self) -> str:
        """比較の左辺（view があれば呼び出し式を差し込んだもの。呼び出しでない式は括弧で包む）。"""
        if not self.view:
            return self.call
        bare = isinstance(ast.parse(self.call, mode="eval").body, ast.Call)
        return self.view.replace("$", self.call if bare else f"({self.call})")


@dataclass
class CaseBank:
    """リポジトリのケースバンク（casebank.json の中身）。"""

    repo: str
    package: str
    seed: int
    header: list[str]
    topics: dict[str, str]
    decoys: list[list[str]]
    readme_refs: dict[str, str]
    edge: dict[str, Any]  # Any: casegen.EDGE の写し（kind・prompt・tag・readme_ref）
    cases: list[Case]

    def to_json(self) -> dict[str, Any]:
        # Any: JSON 辞書
        return {
            "repo": self.repo,
            "package": self.package,
            "seed": self.seed,
            "header": self.header,
            "topics": self.topics,
            "decoys": self.decoys,
            "readme_refs": self.readme_refs,
            "edge": self.edge,
            "cases": [c.to_json() for c in self.cases],
        }

    @staticmethod
    def from_json(d: Mapping[str, Any]) -> CaseBank:
        return CaseBank(
            repo=d["repo"],
            package=d["package"],
            seed=d["seed"],
            header=list(d["header"]),
            topics=dict(d["topics"]),
            decoys=[list(x) for x in d["decoys"]],
            readme_refs=dict(d["readme_refs"]),
            edge=dict(d["edge"]),
            cases=[Case.from_json(c) for c in d["cases"]],
        )

    def by_id(self) -> dict[str, Case]:
        return {c.id: c for c in self.cases}


def repo_dir(repo: str, root: Path | None = None) -> Path:
    """フィクスチャ・リポジトリのディレクトリ（`data/fixtures/repos/<repo>`）。"""
    return (root or REPO_FIXTURE_ROOT) / repo


def dump_json(obj: Any) -> str:  # Any: JSON 化できる値
    """決定的な JSON 文字列（キー順は挿入順、末尾に改行）。"""
    return json.dumps(obj, ensure_ascii=False, indent=_JSON_INDENT) + "\n"


def _slug(target: str) -> str:
    """修飾名をテスト関数名に使える識別子にする（`Money.__add__` -> `money_add`）。"""
    return re.sub(r"_+", "_", target.replace(".", "_")).strip("_").lower()


def _load_casegen(rdir: Path) -> Any:  # Any: 動的に読み込むモジュール
    spec = importlib.util.spec_from_file_location(f"casegen_{rdir.name}", rdir / "casegen.py")
    if spec is None or spec.loader is None:
        raise FileNotFoundError(rdir / "casegen.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_runner(
    mode: str,
    src_dir: Path,
    cases_json: Path,
    *,
    timeout_s: float = PROCESS_TIMEOUT_S,
    case_timeout_s: float = CASE_TIMEOUT_S,
) -> dict[str, dict[str, Any]] | None:
    """runner を別プロセスで走らせて結果の辞書を返す。プロセスが時間切れなら None。"""
    with tempfile.TemporaryDirectory(prefix="bugsynth_out_") as tmp:
        out = Path(tmp) / "out.json"
        env = {
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "BUGSYNTH_CASE_TIMEOUT": str(case_timeout_s),
        }
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-B", "-c", RUNNER_SOURCE, mode, str(src_dir)]
                + [str(cases_json), str(out)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env=env,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return None
        if proc.returncode != 0 or not out.exists():
            return None
        return cast(dict[str, dict[str, Any]], json.loads(out.read_text(encoding="utf-8")))


def _bank_meta_json(bank: CaseBank) -> str:
    return dump_json(bank.to_json())


def build_casebank(repo: str, *, root: Path | None = None, write: bool = True) -> CaseBank:
    """casegen.py の入力を正しいコードで走らせて期待値を凍結し、casebank.json を作る（C1 の前提）。"""
    rdir = repo_dir(repo, root)
    mod = _load_casegen(rdir)
    seed = SELECTION_SEED + REPO_ORDER.index(repo)
    rng = np.random.default_rng(seed)
    raw: list[dict[str, Any]] = list(mod.inputs(rng))  # Any: casegen が返す辞書
    counter: Counter[str] = Counter()
    cases: list[Case] = []
    for item in raw:
        target = str(item["target"])
        counter[target] += 1
        cases.append(
            Case(
                id=f"{_slug(target)}-{counter[target]:03d}",
                target=target,
                module=str(item["module"]),
                setup=tuple(item.get("setup", ())),
                call=str(item["call"]),
                view=item.get("view"),
                expect=None,
                raises=item.get("raises"),
                tags=tuple(item.get("tags", ())),
            )
        )
    package = str(mod.PACKAGE)
    bank = CaseBank(
        repo=repo,
        package=package,
        seed=seed,
        header=list(mod.HEADER),
        topics=dict(mod.TOPICS),
        decoys=[list(x) for x in mod.DECOYS],
        readme_refs=dict(mod.README_REFS),
        edge=dict(mod.EDGE),
        cases=cases,
    )
    with tempfile.TemporaryDirectory(prefix="bugsynth_freeze_") as tmp:
        cj = Path(tmp) / "cases.json"
        cj.write_text(_bank_meta_json(bank), encoding="utf-8")
        res = run_runner("freeze", rdir / "repo" / "src", cj)
    if res is None:
        raise RuntimeError(f"{repo}: 期待値の凍結が時間切れ")
    frozen: list[Case] = []
    errors: list[str] = []
    for c in cases:
        r = res[c.id]
        if r["s"] != "ok":
            errors.append(f"{c.id}: {r.get('a', r['s'])}  [{c.call}]")
            continue
        frozen.append(
            Case(
                id=c.id,
                target=c.target,
                module=c.module,
                setup=c.setup,
                call=c.call,
                view=c.view,
                expect=r.get("expect"),
                raises=r.get("raises"),
                tags=c.tags,
            )
        )
    if errors:
        raise ValueError(f"{repo}: 凍結できないケースがある:\n" + "\n".join(errors))
    bank.cases = frozen
    if write:
        (rdir / "casebank.json").write_text(_bank_meta_json(bank), encoding="utf-8")
    return bank


def load_casebank(repo: str, *, root: Path | None = None) -> CaseBank:
    """凍結済みの casebank.json を読む。"""
    text = (repo_dir(repo, root) / "casebank.json").read_text(encoding="utf-8")
    return CaseBank.from_json(json.loads(text))


# --- リテラルの正規化（CONTENT.md 6.2 節。構成検査 C6・C13 と識別的リテラルの判定に使う） ------
def _num_form(x: object) -> str | None:
    """数値の正規形（`Decimal(str(x)).normalize()` の文字列）。数値でなければ None。"""
    try:
        d = Decimal(str(x).strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite():
        return None
    return str(d.normalize())


_STOP_NUM_FORMS: Final = frozenset(
    [cast(str, _num_form(n)) for n in TEXT_STOP_NUMBERS] + [str(n) for n in TEXT_STOP_NUMBERS]
)


def norm_constant(value: object) -> set[str]:
    """`ast.Constant` の値を正規化した文字列の集合にする。bool・None・bytes は無視する。"""
    if value is None or isinstance(value, (bool, bytes)):
        return set()
    if isinstance(value, (int, float)):
        form = _num_form(value)
        return {form} if form is not None else set()
    if isinstance(value, str):
        out = {value}
        form = _num_form(value)
        if form is not None:
            out.add(form)
        return out
    if isinstance(value, (tuple, list, set, frozenset)):
        out2: set[str] = set()
        for v in value:
            out2 |= norm_constant(v)
        return out2
    return set()


def constants_of(tree: ast.AST, *, lines: set[int] | None = None) -> set[str]:
    """AST の `ast.Constant` を正規化して集める。lines を渡せばその行にある定数だけ。"""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if lines is not None and node.lineno not in lines:
                continue
            out |= norm_constant(node.value)
    return out


def identifiers_of(tree: ast.AST) -> set[str]:
    """ファイルに現れる識別子（名前・属性名・定義名・引数名）。停止リストの一部になる。"""
    ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            ids.add(node.id)
        elif isinstance(node, ast.Attribute):
            ids.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ids.add(node.name)
        elif isinstance(node, ast.arg):
            ids.add(node.arg)
        elif isinstance(node, ast.alias):
            ids.add(node.asname or node.name)
    return ids


def drop_stop(literals: Iterable[str], idents: set[str]) -> set[str]:
    """停止リスト（0・1・2・10・100、空・空白・1 文字の文字列、ファイルの識別子）を除く。"""
    out: set[str] = set()
    for s in literals:
        if s in _STOP_NUM_FORMS or len(s.strip()) <= 1 or s in idents:
            continue
        out.add(s)
    return out


# --- ソースの索引 ---------------------------------------------------------------
@dataclass(frozen=True)
class FuncInfo:
    """モジュール直下の関数、またはクラスのメソッド（入れ子の関数は外側に含める）。"""

    module: str
    qualname: str
    lineno: int
    end_lineno: int
    params: tuple[tuple[str, str], ...]  # (名前, 注釈の文字列)。self / cls は含めない
    is_method: bool

    @property
    def key(self) -> str:
        return f"{self.module}.{self.qualname}"


@dataclass
class SourceFile:
    """1 つのモジュールのソースと索引。"""

    module: str
    rel: str  # 作業ツリー相対のパス（src/<pkg>/<module>.py）
    text: str
    tree: ast.Module
    bsrc: bytes
    line_starts: list[int]
    owners: list[tuple[FuncInfo, ast.FunctionDef | ast.AsyncFunctionDef]]

    def off(self, lineno: int, col: int) -> int:
        """(行, UTF-8 のバイト桁) をバイト位置にする。"""
        return self.line_starts[lineno - 1] + col

    def span(self, node: ast.expr | ast.stmt) -> tuple[int, int]:
        assert node.end_lineno is not None and node.end_col_offset is not None
        return self.off(node.lineno, node.col_offset), self.off(
            node.end_lineno, node.end_col_offset
        )

    def seg(self, node: ast.expr | ast.stmt) -> str:
        s, e = self.span(node)
        return self.bsrc[s:e].decode("utf-8")


def _params_of(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool
) -> tuple[tuple[str, str], ...]:
    pos = [*fn.args.posonlyargs, *fn.args.args]
    if is_method and pos and pos[0].arg in ("self", "cls"):
        pos = pos[1:]
    return tuple(
        (a.arg, ast.unparse(a.annotation) if a.annotation is not None else "") for a in pos
    )


def parse_source(text: str, module: str, rel: str) -> SourceFile:
    """ソースを構文解析し、関数の索引を作る。"""
    tree = ast.parse(text)
    bsrc = text.encode("utf-8")
    starts = [0]
    for m in re.finditer(rb"\n", bsrc):
        starts.append(m.end())
    owners: list[tuple[FuncInfo, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(body: list[ast.stmt], prefix: str, in_class: bool) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.", True)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.end_lineno is not None
                info = FuncInfo(
                    module=module,
                    qualname=f"{prefix}{node.name}",
                    lineno=node.lineno,
                    end_lineno=node.end_lineno,
                    params=_params_of(node, in_class),
                    is_method=in_class,
                )
                owners.append((info, node))

    visit(tree.body, "", False)
    return SourceFile(module, rel, text, tree, bsrc, starts, owners)


def load_sources(src_pkg_dir: Path, package: str) -> dict[str, SourceFile]:
    """パッケージ直下の `.py`（`__init__.py` を除く）を読む。キーはモジュール名。"""
    out: dict[str, SourceFile] = {}
    for path in sorted(src_pkg_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        out[path.stem] = parse_source(
            path.read_text(encoding="utf-8"), path.stem, f"src/{package}/{path.name}"
        )
    return out


def collect_enums(sources: Mapping[str, SourceFile]) -> dict[str, list[str]]:
    """Enum を継承したクラスの名前 -> メンバー名の並び。"""
    enums: dict[str, list[str]] = {}
    for sf in sources.values():
        for node in ast.walk(sf.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [ast.unparse(b) for b in node.bases]
            if not any(b == "Enum" or b.endswith(".Enum") for b in bases):
                continue
            members = [
                t.id
                for st in node.body
                if isinstance(st, ast.Assign)
                for t in st.targets
                if isinstance(t, ast.Name)
            ]
            enums[node.name] = members
    return enums


# --- 変異候補の列挙 ---------------------------------------------------------------
Edit = tuple[int, int, str]  # (開始バイト, 終了バイト, 置換テキスト)


@dataclass(frozen=True)
class Candidate:
    """変異候補（ファイル・関数・位置・オペレータ・置換）。"""

    cid: str
    operator: str
    module: str
    file: str
    function: str
    lineno: int
    before: str
    after: str
    edits: tuple[Edit, ...]
    excluded: str | None  # 静的な等価除外の理由（CONTENT.md 3.1 節）。None なら残す

    @property
    def key(self) -> str:
        return f"{self.module}.{self.function}"


_CMP_SWAP: Final = {"<": "<=", "<=": "<", ">": ">=", ">=": ">"}
_ARITH_SWAP: Final = {"+": "-", "-": "+", "*": "/", "/": "*", "//": "/"}
_ARITH_RE: Final = {
    ast.Add: (r"\+", "+"),
    ast.Sub: (r"-", "-"),
    ast.Mult: (r"\*(?!\*)", "*"),
    ast.Div: (r"/(?!/)", "/"),
    ast.FloorDiv: (r"//", "//"),
}
_ROUND_CONST_SWAP: Final = {
    "ROUND_HALF_EVEN": "ROUND_HALF_UP",
    "ROUND_HALF_UP": "ROUND_HALF_EVEN",
    "ROUND_DOWN": "ROUND_UP",
    "ROUND_UP": "ROUND_DOWN",
    "ROUND_CEILING": "ROUND_FLOOR",
    "ROUND_FLOOR": "ROUND_CEILING",
}
_ROUND_MEMBER_SWAP: Final = {
    "HALF_EVEN": "HALF_UP",
    "HALF_UP": "HALF_EVEN",
    "DOWN": "UP",
    "UP": "DOWN",
}
_FLOOR_CEIL_SWAP: Final = {"ceil": "floor", "floor": "ceil"}
_BUILTIN_SWAP: Final = {"min": "max", "max": "min", "any": "all", "all": "any"}


def _is_num_const(n: ast.expr, values: tuple[int, ...] | None = None) -> bool:
    if not isinstance(n, ast.Constant) or isinstance(n.value, bool):
        return False
    if not isinstance(n.value, (int, float)):
        return False
    return values is None or n.value in values


def _is_none_const(n: ast.expr) -> bool:
    return isinstance(n, ast.Constant) and n.value is None


def _paren(sf: SourceFile, n: ast.expr) -> str:
    text = sf.seg(n)
    simple = (ast.Name, ast.Attribute, ast.Call, ast.Subscript, ast.Constant)
    return text if isinstance(n, simple) else f"({text})"


class _Enumerator:
    """1 つのモジュールから 9 種のオペレータの候補を列挙する。"""

    def __init__(
        self,
        sf: SourceFile,
        func_by_name: Mapping[str, list[FuncInfo]],
        enums: Mapping[str, list[str]],
    ) -> None:
        self.sf = sf
        self.func_by_name = func_by_name
        self.enums = enums
        self.has_math = any(
            isinstance(n, ast.Import) and any(a.name == "math" for a in n.names)
            for n in ast.walk(sf.tree)
        )
        self.out: list[Candidate] = []

    def _add(
        self,
        info: FuncInfo,
        op: str,
        variant: str,
        node: ast.expr | ast.stmt,
        edits: list[Edit],
        excluded: str | None = None,
    ) -> None:
        first = min(e[0] for e in edits)
        lineno = bisect.bisect_right(self.sf.line_starts, first)
        col = first - self.sf.line_starts[lineno - 1]
        mutated = apply_edits(self.sf.bsrc, edits)
        after_lines = mutated.decode("utf-8", errors="replace").splitlines()
        before = self.sf.text.splitlines()[lineno - 1].strip()
        after = after_lines[lineno - 1].strip() if lineno - 1 < len(after_lines) else ""
        self.out.append(
            Candidate(
                cid=f"{self.sf.module}@{lineno}:{col}:{op}:{variant}",
                operator=op,
                module=self.sf.module,
                file=self.sf.rel,
                function=info.qualname,
                lineno=lineno,
                before=before,
                after=after,
                edits=tuple(sorted(edits)),
                excluded=excluded,
            )
        )

    def run(self) -> list[Candidate]:
        for info, fn in self.sf.owners:
            self._defaults(info, fn)
            body = list(fn.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]  # docstring
            parents: dict[ast.AST, ast.AST] = {}
            nodes: list[ast.AST] = []
            for st in body:
                for n in ast.walk(st):
                    nodes.append(n)
                    for ch in ast.iter_child_nodes(n):
                        parents[ch] = n
            for n in nodes:
                self._visit(info, n, parents)
        return self.out

    # --- ノードごとの処理 ---
    def _visit(self, info: FuncInfo, n: ast.AST, parents: dict[ast.AST, ast.AST]) -> None:
        if isinstance(n, ast.Compare):
            self._cmp(info, n)
        if isinstance(n, ast.BinOp):
            self._binop(info, n)
        if isinstance(n, ast.AugAssign):
            self._augassign(info, n)
        if isinstance(n, ast.Constant):
            self._int_const(info, n, parents)
        if isinstance(n, ast.Call):
            self._call(info, n)
        if isinstance(n, ast.Slice):
            self._slice(info, n)
        if isinstance(n, ast.If):
            self._if_none(info, n)
        if isinstance(n, ast.IfExp):
            self._ifexp_none(info, n)
        if isinstance(n, ast.BoolOp):
            self._boolop(info, n)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            self._not(info, n)
        if isinstance(n, ast.Name) and n.id in _ROUND_CONST_SWAP:
            self._round_name(info, n)
        if isinstance(n, ast.Attribute):
            self._round_attr(info, n)

    def _between(self, left: ast.expr, right: ast.expr) -> tuple[int, str]:
        """left の終端から right の始端までのバイト列（演算子の探索範囲）。"""
        start = self.sf.span(left)[1]
        end = self.sf.span(right)[0]
        return start, self.sf.bsrc[start:end].decode("utf-8")

    # O1 cmp_boundary
    def _cmp(self, info: FuncInfo, n: ast.Compare) -> None:
        operands = [n.left, *n.comparators]
        for i, op in enumerate(n.ops):
            sym = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}.get(type(op))
            if sym is None:
                continue
            base, seg = self._between(operands[i], operands[i + 1])
            m = re.search(r"<=|>=|<|>", seg)
            if m is None or m.group(0) != sym:
                continue
            excl = (
                "定数どうしの比較"
                if isinstance(operands[i], ast.Constant)
                and isinstance(operands[i + 1], ast.Constant)
                else None
            )
            edit = (base + m.start(), base + m.end(), _CMP_SWAP[sym])
            self._add(info, "cmp_boundary", str(i), n, [edit], excl)

    # O2 arith_op
    def _binop(self, info: FuncInfo, n: ast.BinOp) -> None:
        pat = _ARITH_RE.get(type(n.op))
        if pat is None:
            return
        regex, sym = pat
        base, seg = self._between(n.left, n.right)
        m = re.search(regex, seg)
        if m is None:
            return
        excl: str | None = None
        if sym in "+-" and (_is_num_const(n.left, (0,)) or _is_num_const(n.right, (0,))):
            excl = "0 の加減算"
        elif sym in ("*", "/", "//") and (
            _is_num_const(n.right, (1,)) or _is_num_const(n.left, (1,))
        ):
            excl = "1 の乗除算"
        elif sym == "+" and any(
            isinstance(x, (ast.JoinedStr, ast.Constant)) and not _is_num_const(x)
            for x in (n.left, n.right)
        ):
            excl = "文字列の連結"
        self._add(
            info, "arith_op", "x", n, [(base + m.start(), base + m.end(), _ARITH_SWAP[sym])], excl
        )

    def _augassign(self, info: FuncInfo, n: ast.AugAssign) -> None:
        pat = _ARITH_RE.get(type(n.op))
        if pat is None:
            return
        regex, sym = pat
        base, seg = self._between(n.target, n.value)
        m = re.search(regex + "=", seg)
        if m is None:
            return
        excl = "0 の加減算" if sym in "+-" and _is_num_const(n.value, (0,)) else None
        self._add(
            info,
            "arith_op",
            "aug",
            n,
            [(base + m.start(), base + m.end(), _ARITH_SWAP[sym] + "=")],
            excl,
        )

    # O3 off_by_one（整数定数・range の端・スライスの端）
    def _int_const(self, info: FuncInfo, n: ast.Constant, parents: dict[ast.AST, ast.AST]) -> None:
        if not isinstance(n.value, int) or isinstance(n.value, bool):
            return
        parent = parents.get(n)
        ok = isinstance(parent, (ast.BinOp, ast.Compare, ast.Slice)) or (
            isinstance(parent, ast.Subscript) and parent.slice is n
        )
        if (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "range"
        ):
            ok = True
        if not ok:
            return
        s, e = self.sf.span(n)
        for delta, label in ((1, "p"), (-1, "m")):
            self._add(info, "off_by_one", f"c{label}", n, [(s, e, str(n.value + delta))])

    def _slice(self, info: FuncInfo, n: ast.Slice) -> None:
        for bound, label in ((n.lower, "lo"), (n.upper, "up")):
            if bound is None or isinstance(bound, ast.Constant):
                continue
            s, e = self.sf.span(bound)
            text = _paren(self.sf, bound)
            for sign in ("+", "-"):
                self._add(
                    info, "off_by_one", f"s{label}{sign}", bound, [(s, e, f"{text} {sign} 1")]
                )

    # O4 arg_swap / O9 builtin_swap / O7 の関数名
    def _call(self, info: FuncInfo, n: ast.Call) -> None:
        f = n.func
        if isinstance(f, ast.Name) and f.id in _BUILTIN_SWAP:
            s, e = self.sf.span(f)
            self._add(info, "builtin_swap", f.id, f, [(s, e, _BUILTIN_SWAP[f.id])])
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if name in _FLOOR_CEIL_SWAP and (
            isinstance(f, ast.Name)
            or (isinstance(f, ast.Attribute) and ast.unparse(f.value) == "math")
        ):
            s, e = self.sf.span(f)
            text = self.sf.seg(f)
            self._add(
                info, "rounding_mode", name, f, [(s, e, text.replace(name, _FLOOR_CEIL_SWAP[name]))]
            )
        if isinstance(f, ast.Name) and f.id == "round" and self.has_math:
            s, e = self.sf.span(f)
            self._add(info, "rounding_mode", "round_floor", f, [(s, e, "math.floor")])
        if (
            isinstance(f, ast.Name)
            and f.id == "range"
            and len(n.args) >= 2
            and not isinstance(n.args[-1], (ast.Constant, ast.Starred))
        ):
            last = n.args[-1]
            s, e = self.sf.span(last)
            text = _paren(self.sf, last)
            for sign in ("+", "-"):
                self._add(info, "off_by_one", f"r{sign}", last, [(s, e, f"{text} {sign} 1")])
        if name is None or len(n.args) < 2 or any(isinstance(a, ast.Starred) for a in n.args):
            return
        cands = self.func_by_name.get(name, [])
        if len(cands) != 1:
            return
        params = cands[0].params
        for i in range(len(n.args)):
            for j in range(i + 1, len(n.args)):
                if j >= len(params) or not params[i][1] or params[i][1] != params[j][1]:
                    continue
                si, ei = self.sf.span(n.args[i])
                sj, ej = self.sf.span(n.args[j])
                ti, tj = self.sf.seg(n.args[i]), self.sf.seg(n.args[j])
                self._add(info, "arg_swap", f"{i}{j}", n, [(si, ei, tj), (sj, ej, ti)])

    # O5 none_guard_drop
    def _if_none(self, info: FuncInfo, n: ast.If) -> None:
        t = n.test
        if (
            n.orelse
            or not isinstance(t, ast.Compare)
            or len(t.ops) != 1
            or not isinstance(t.ops[0], ast.Is)
            or not _is_none_const(t.comparators[0])
        ):
            return
        s, e = self.sf.span(n)
        self._add(info, "none_guard_drop", "if", n, [(s, e, "pass")])

    def _ifexp_none(self, info: FuncInfo, n: ast.IfExp) -> None:
        t = n.test
        if (
            not isinstance(t, ast.Compare)
            or len(t.ops) != 1
            or not _is_none_const(t.comparators[0])
        ):
            return
        if isinstance(t.ops[0], ast.IsNot):
            keep = n.body
        elif isinstance(t.ops[0], ast.Is):
            keep = n.orelse
        else:
            return
        s, e = self.sf.span(n)
        self._add(info, "none_guard_drop", "ifexp", n, [(s, e, _paren(self.sf, keep))])

    # O8 bool_op
    def _boolop(self, info: FuncInfo, n: ast.BoolOp) -> None:
        word = "and" if isinstance(n.op, ast.And) else "or"
        other = "or" if word == "and" else "and"
        for i in range(len(n.values) - 1):
            base, seg = self._between(n.values[i], n.values[i + 1])
            m = re.search(rf"\b{word}\b", seg)
            if m is None:
                continue
            self._add(info, "bool_op", f"w{i}", n, [(base + m.start(), base + m.end(), other)])
        # `x or default` から or を外す（None・欠損の早期処理を落とす O5 の一種）
        if (
            word == "or"
            and len(n.values) == 2
            and isinstance(n.values[0], (ast.Name, ast.Attribute))
        ):
            s, e = self.sf.span(n)
            self._add(info, "none_guard_drop", "or", n, [(s, e, self.sf.seg(n.values[0]))])

    def _not(self, info: FuncInfo, n: ast.UnaryOp) -> None:
        s = self.sf.span(n)[0]
        e = self.sf.span(n.operand)[0]
        self._add(info, "bool_op", "not", n, [(s, e, "")])

    # O7 rounding_mode
    def _round_name(self, info: FuncInfo, n: ast.Name) -> None:
        s, e = self.sf.span(n)
        self._add(info, "rounding_mode", n.id, n, [(s, e, _ROUND_CONST_SWAP[n.id])])

    def _round_attr(self, info: FuncInfo, n: ast.Attribute) -> None:
        s, e = self.sf.span(n)
        if n.attr in _ROUND_CONST_SWAP:
            self._add(
                info,
                "rounding_mode",
                n.attr,
                n,
                [(s, e, self.sf.seg(n).replace(n.attr, _ROUND_CONST_SWAP[n.attr]))],
            )
        elif (
            isinstance(n.value, ast.Name)
            and n.value.id == "RoundingMode"
            and n.attr in _ROUND_MEMBER_SWAP
        ):
            self._add(
                info,
                "rounding_mode",
                n.attr,
                n,
                [(s, e, f"RoundingMode.{_ROUND_MEMBER_SWAP[n.attr]}")],
            )

    # O6 default_value
    def _defaults(self, info: FuncInfo, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        a = fn.args
        pos = [*a.posonlyargs, *a.args]
        pairs: list[tuple[str, ast.expr]] = list(
            zip((x.arg for x in pos[len(pos) - len(a.defaults) :]), a.defaults, strict=True)
        )
        pairs += [
            (k.arg, d) for k, d in zip(a.kwonlyargs, a.kw_defaults, strict=True) if d is not None
        ]
        for name, d in pairs:
            new = self._alt_default(fn, name, d)
            if new is None:
                continue
            s, e = self.sf.span(d)
            self._add(info, "default_value", name, d, [(s, e, new)])

    def _alt_default(
        self, fn: ast.FunctionDef | ast.AsyncFunctionDef, name: str, d: ast.expr
    ) -> str | None:
        if isinstance(d, ast.Constant):
            v = d.value
            if isinstance(v, bool):
                return str(not v)
            if isinstance(v, int):
                return str(v + 1)
            if isinstance(v, float):
                return repr(v * 2 if v else 1.0)
            if isinstance(v, str):
                alts: set[str] = set()
                for n in ast.walk(fn):
                    if (
                        isinstance(n, ast.Compare)
                        and isinstance(n.left, ast.Name)
                        and n.left.id == name
                    ):
                        for c in n.comparators:
                            for k in ast.walk(c):
                                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                    alts.add(k.value)
                alts.discard(v)
                return repr(min(alts)) if alts else None
            return None
        if isinstance(d, ast.Attribute) and isinstance(d.value, ast.Name):
            members = self.enums.get(d.value.id)
            if members and d.attr in members:
                nxt = members[(members.index(d.attr) + 1) % len(members)]
                return f"{d.value.id}.{nxt}"
        return None


def apply_edits(bsrc: bytes, edits: Iterable[Edit]) -> bytes:
    """バイト列に置換を当てる（重なる置換は ValueError）。"""
    out = bsrc
    ordered = sorted(edits, reverse=True)
    prev_start = len(bsrc) + 1
    for s, e, text in ordered:
        if e > prev_start:
            raise ValueError("置換が重なっている")
        out = out[:s] + text.encode("utf-8") + out[e:]
        prev_start = s
    return out


def enumerate_candidates(sources: Mapping[str, SourceFile]) -> list[Candidate]:
    """全モジュールの変異候補を列挙する（CONTENT.md 5.2 節の 2）。"""
    func_by_name: dict[str, list[FuncInfo]] = defaultdict(list)
    for sf in sources.values():
        for info, _ in sf.owners:
            func_by_name[info.qualname.rsplit(".", 1)[-1]].append(info)
    enums = collect_enums(sources)
    out: list[Candidate] = []
    for mod in sorted(sources):
        out.extend(_Enumerator(sources[mod], func_by_name, enums).run())
    return sorted(out, key=lambda c: (c.module, c.lineno, c.cid))


# --- テストの描画 -----------------------------------------------------------------
def render_test(case: Case) -> str:
    """ケースを、リテラルを直接書いた平の pytest 関数にする（CONTENT.md 5.3 節）。"""
    lines = [f"def {case.test_name}():"]
    lines.extend(f"    {s}" for s in case.setup)
    if case.raises:
        lines.append(f"    with pytest.raises({case.raises}):")
        lines.append(f"        {case.call}")
    else:
        lines.append(f"    assert {case.expression()} == {case.expect}")
    return "\n".join(lines) + "\n"


def test_file_header(bank: CaseBank, canary: str | None = None) -> str:
    """テストファイルの先頭（任意のカナリア行、import）。"""
    head = [f"# canary GUID {canary}"] if canary else []
    head.extend(bank.header)
    head.append(f"from {bank.package} import *  # noqa: F403")
    return "\n".join(head) + "\n\n\n"


def render_test_file(bank: CaseBank, cases: Sequence[Case], canary: str | None = None) -> str:
    """ケースの並びを 1 つのテストファイルにする。"""
    return test_file_header(bank, canary) + "\n\n".join(render_test(c) for c in cases)


def case_literals(case: Case) -> set[str]:
    """テスト 1 本に現れるリテラル（正規化済み。停止リスト前）。"""
    return constants_of(ast.parse(render_test(case)))


# --- 解析（候補の評価・呼び出しグラフ・特殊化の材料） ------------------------------------
@dataclass
class MutantRun:
    """候補を入れたコードでケースバンクを走らせた結果。"""

    cand: Candidate
    status: str  # ok | syntax | timeout | crash
    results: dict[str, tuple[str, str]] = field(
        default_factory=dict
    )  # ケース ID -> (状態, 実際の値)

    @property
    def killed(self) -> list[str]:
        """殺す集合 K（合格しなかったケースの ID）。"""
        return sorted(k for k, (s, _) in self.results.items() if s != "pass")


@dataclass
class Analysis:
    """1 リポジトリの解析結果。"""

    repo: str
    bank: CaseBank
    sources: dict[str, SourceFile]
    candidates: list[Candidate]
    runs: dict[str, MutantRun]
    edges: dict[str, set[tuple[str, str]]]  # 公開関数のキー -> 観測した呼び出しの辺
    spec: dict[str, dict[str, Any]]  # ケース ID -> specinfo（runner の spec モード）
    lits: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.lits:
            self.lits = {c.id: case_literals(c) for c in self.bank.cases}

    def decoy_keys(self) -> set[str]:
        return {k for pair in self.bank.decoys for k in pair}

    def discriminative(self, case_id: str, module: str) -> set[str]:
        """ケースの識別的リテラル（停止リストと module のファイルに現れるリテラルを除く。CONTENT.md 0 章）。"""
        sf = self.sources[module]
        return drop_stop(self.lits[case_id], identifiers_of(sf.tree)) - constants_of(sf.tree)

    def distance(self, target_key: str, func_key: str) -> int | None:
        """公開関数から関数までの呼び出しの段数（観測した辺の最短経路。同じなら 0）。"""
        if target_key == func_key:
            return 0
        adj: dict[str, list[str]] = defaultdict(list)
        for a, b in sorted(self.edges.get(target_key, set())):
            adj[a].append(b)
        seen = {target_key}
        frontier = [target_key]
        d = 0
        while frontier:
            d += 1
            nxt: list[str] = []
            for n in frontier:
                for m in adj[n]:
                    if m == func_key:
                        return d
                    if m not in seen:
                        seen.add(m)
                        nxt.append(m)
            frontier = nxt
        return None


def _write_cases_json(bank: CaseBank, path: Path) -> None:
    path.write_text(_bank_meta_json(bank), encoding="utf-8")


def run_mutant(
    rdir: Path, bank: CaseBank, cand: Candidate, sf: SourceFile, cases_json: Path
) -> MutantRun:
    """候補を入れた一時コピーでケースバンクを走らせる。"""
    mutated = apply_edits(sf.bsrc, cand.edits)
    try:
        compile(mutated.decode("utf-8"), cand.file, "exec")
    except SyntaxError:
        return MutantRun(cand, "syntax")
    with tempfile.TemporaryDirectory(prefix="bugsynth_mut_") as tmp:
        src = Path(tmp) / "src"
        shutil.copytree(rdir / "repo" / "src", src, ignore=shutil.ignore_patterns("__pycache__"))
        (Path(tmp) / cand.file).write_bytes(mutated)
        res = run_runner("run", src, cases_json)
    if res is None:
        return MutantRun(cand, "timeout")
    return MutantRun(cand, "ok", {cid: (r["s"], r.get("a", "")) for cid, r in res.items()})


def _cache_key(rdir: Path, bank: CaseBank) -> str:
    h = hashlib.sha256()
    for p in sorted((rdir / "repo" / "src").rglob("*.py")):
        h.update(p.read_bytes())
    h.update(json.dumps([c.to_json() for c in bank.cases], ensure_ascii=False).encode())
    h.update(json.dumps([bank.package, bank.header]).encode())
    h.update(hashlib.sha256(RUNNER_SOURCE.encode()).digest())
    return h.hexdigest()[:16]


def analyze(
    repo: str,
    *,
    root: Path | None = None,
    workers: int = 8,
    cache_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Analysis:
    """候補の列挙・評価、呼び出しグラフ、特殊化の材料を作る（CONTENT.md 5.2 節の 1〜4）。"""
    rdir = repo_dir(repo, root)
    bank = load_casebank(repo, root=root)
    sources = load_sources(rdir / "repo" / "src" / bank.package, bank.package)
    cands = enumerate_candidates(sources)
    cache_file = cache_dir / f"{repo}-{_cache_key(rdir, bank)}.json" if cache_dir else None
    cached: dict[str, Any] | None = None  # Any: キャッシュの JSON
    if cache_file is not None and cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="bugsynth_an_") as tmp:
        cj = Path(tmp) / "cases.json"
        _write_cases_json(bank, cj)
        src = rdir / "repo" / "src"
        base = run_runner("run", src, cj)
        if base is None or any(r["s"] != "pass" for r in base.values()):
            bad = [k for k, r in (base or {}).items() if r["s"] != "pass"]
            raise AssertionError(f"{repo}: 正しいコードでケースバンクが通らない (C1): {bad[:10]}")
        if cached is not None:
            edges_raw = cached["edges"]
            spec = cached["spec"]
            runs = {
                cid: MutantRun(
                    next(c for c in cands if c.cid == cid),
                    r["status"],
                    {k: (v[0], v[1]) for k, v in r["results"].items()},
                )
                for cid, r in cached["runs"].items()
            }
        else:
            tr = run_runner("trace", src, cj)
            sp = run_runner("spec", src, cj)
            if tr is None or sp is None:
                raise RuntimeError(f"{repo}: trace / spec が時間切れ")
            edges_raw = {cid: r.get("edges", []) for cid, r in tr.items()}
            spec = sp
            live = [c for c in cands if c.excluded is None]
            runs = {}
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    c.cid: ex.submit(run_mutant, rdir, bank, c, sources[c.module], cj) for c in live
                }
                for i, (cid, fut) in enumerate(futs.items()):
                    runs[cid] = fut.result()
                    if progress and i % 50 == 0:
                        progress(f"{repo}: {i}/{len(futs)}")
            if cache_file is not None:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(
                    json.dumps(
                        {
                            "edges": edges_raw,
                            "spec": spec,
                            "runs": {
                                cid: {"status": r.status, "results": r.results}
                                for cid, r in runs.items()
                            },
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
    edges: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for c in bank.cases:
        for a, b in edges_raw.get(c.id, []):
            edges[c.key].add((a, b))
    return Analysis(repo, bank, sources, cands, runs, dict(edges), spec)


# --- タスクの計画（CONTENT.md 3.2・4・5.2 節の 6〜9） -------------------------------------
@dataclass(frozen=True)
class Plan:
    """候補 1 件と経路 A を決めたときのタスクの設計（選択前）。"""

    cand: Candidate
    level: str
    route_a: str  # 公開関数の修飾名
    route_module: str
    distance: int
    manifestation: str
    decoy: bool
    visible_fail: tuple[str, ...]
    kill: tuple[str, ...]
    base_visible: tuple[str, ...]
    discriminative: tuple[str, ...]
    hidden_routes: int
    k_size: int
    mutation_score: float
    seed: int


def _plan_seed(repo: str, cid: str, target: str) -> int:
    return zlib.crc32(f"{repo}|{cid}|{target}".encode()) ^ SELECTION_SEED


def exclusion_reason(an: Analysis, cand: Candidate) -> str | None:
    """候補を除く理由（静的・動的。CONTENT.md 3.1・3.2 節）。残すなら None。"""
    if cand.excluded is not None:
        return f"static:{cand.excluded}"
    run = an.runs[cand.cid]
    if run.status != "ok":
        return f"dynamic:{run.status}"
    k = run.killed
    if not k:
        return "dynamic:not_killed"
    if len(k) / len(an.bank.cases) > NOISY_RATIO:
        return "dynamic:noisy"
    return None


def make_plans(an: Analysis, cand: Candidate) -> list[Plan]:
    """候補を各公開関数（経路 A）に当てて、水準 L1〜L3 の設計が成り立つものを返す。"""
    if exclusion_reason(an, cand) is not None:
        return []
    run = an.runs[cand.cid]
    killed = set(run.killed)
    by_id = an.bank.by_id()
    targets = sorted({by_id[k].target for k in killed})
    plans: list[Plan] = []
    for target in targets:
        t_cases = sorted(
            (by_id[k] for k in killed if by_id[k].target == target), key=lambda c: c.id
        )
        module = t_cases[0].module
        t_key = f"{module}.{target}"
        d = an.distance(t_key, cand.key)
        if d is None or d > 2:
            continue
        level = LEVELS[d]
        if d >= 1 and cand.module == module:
            continue
        decoy = cand.key in an.decoy_keys()
        if (level == "L1" and decoy) or (level == "L3" and not decoy):
            continue
        h = LEVEL_KILL[level]
        if len(killed) < 1 + h:
            continue
        rng = np.random.default_rng(_plan_seed(an.repo, cand.cid, target))
        # 可視の失敗ケース: 識別的リテラルを持ち、特殊化パッチの材料が揃うもの
        eligible = [
            c
            for c in t_cases
            if c.raises is None
            and an.spec.get(c.id, {}).get("s") == "ok"
            and an.spec[c.id].get("result") is not None
            and an.discriminative(c.id, cand.module)
            and (level == "L1" or run.results[c.id][0] == "fail")
        ]
        if not eligible:
            continue
        n_fail = min(len(eligible), int(rng.integers(1, 3)))
        vis_idx = sorted(int(i) for i in rng.choice(len(eligible), size=n_fail, replace=False))
        visible_fail = [eligible[i] for i in vis_idx]
        disc: set[str] = set()
        for c in visible_fail:
            disc |= an.discriminative(c.id, cand.module)
        vis_ids = {c.id for c in visible_fail}
        pool = [
            by_id[k]
            for k in sorted(killed)
            if k not in vis_ids and not (an.discriminative(k, cand.module) & disc)
        ]
        other = [c for c in pool if c.target != target]
        if len(pool) < h or len(other) < LEVEL_CROSS[level]:
            continue
        need = LEVEL_CROSS[level]
        chosen: list[Case] = []
        if need:
            idx = rng.choice(len(other), size=need, replace=False)
            chosen = [other[int(i)] for i in sorted(idx)]
        rest = [c for c in pool if c not in chosen]
        idx2 = rng.choice(len(rest), size=h - len(chosen), replace=False)
        chosen += [rest[int(i)] for i in sorted(idx2)]
        kill = sorted(c.id for c in chosen)
        # ベースの可視テスト: バンク − K から 25〜40 本。各公開関数 1 本以上、経路 A は 3 本以上
        rest_cases = [c for c in an.bank.cases if c.id not in killed]
        base = _choose_base_visible(rest_cases, target, rng)
        if base is None:
            continue
        hidden_ids = {c.id for c in rest_cases} | set(kill)
        live_runs = [r for r in an.runs.values() if r.status == "ok"]
        survivors = sum(1 for r in live_runs if set(r.killed) & hidden_ids)
        score = survivors / len(live_runs) if live_runs else 0.0
        manifestation = (
            "exception" if any(run.results[c.id][0] != "fail" for c in visible_fail) else "value"
        )
        plans.append(
            Plan(
                cand=cand,
                level=level,
                route_a=target,
                route_module=module,
                distance=d,
                manifestation=manifestation,
                decoy=decoy,
                visible_fail=tuple(sorted(vis_ids)),
                kill=tuple(kill),
                base_visible=tuple(base),
                discriminative=tuple(sorted(disc)),
                hidden_routes=sum(1 for c in chosen if c.target != target),
                k_size=len(killed),
                mutation_score=round(score, 4),
                seed=_plan_seed(an.repo, cand.cid, target) & 0x7FFFFFFF,
            )
        )
    return plans


def _choose_base_visible(
    rest: Sequence[Case], target: str, rng: np.random.Generator
) -> list[str] | None:
    """バンク − K から可視テストの回帰分を選ぶ（CONTENT.md 5.2 節の 9）。"""
    n = int(rng.integers(25, 41))
    n = min(n, len(rest))
    chosen: dict[str, Case] = {}
    by_target: dict[str, list[Case]] = defaultdict(list)
    for c in rest:
        by_target[c.target].append(c)
    for t in sorted(by_target):
        group = by_target[t]
        take = min(3 if t == target else 1, len(group))
        for i in sorted(int(x) for x in rng.choice(len(group), size=take, replace=False)):
            chosen[group[i].id] = group[i]
    remaining = [c for c in rest if c.id not in chosen]
    need = n - len(chosen)
    if need > 0 and remaining:
        for i in sorted(
            int(x)
            for x in rng.choice(len(remaining), size=min(need, len(remaining)), replace=False)
        ):
            chosen[remaining[i].id] = remaining[i]
    if len(chosen) < 25 or sum(1 for c in chosen.values() if c.target == target) < min(
        3, len(by_target.get(target, []))
    ):
        return None
    order = {c.id: i for i, c in enumerate(rest)}
    return sorted(chosen, key=lambda k: order[k])


# --- 特殊化パッチ（構成検査 C4。CONTENT.md 4.2 節・5.2 節の 13） -------------------------------
def _module_names(sf: SourceFile) -> set[str]:
    """モジュール直下で名前が使えるもの（import・クラス・関数・代入）。"""
    names: set[str] = set()
    for node in sf.tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _find_owner(sf: SourceFile, qualname: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for info, node in sf.owners:
        if info.qualname == qualname:
            return node
    raise KeyError(f"{sf.module}: {qualname} が無い")


def build_spec_patch(
    base_text: str, module: str, rel: str, qualname: str, infos: Sequence[Mapping[str, Any]]
) -> str | None:
    """可視の失敗ケースの引数なら期待値を返す分岐を、公開関数の先頭に入れた全文を返す。

    infos は runner の spec モードの結果（条件と結果を式にしたもの）。材料が足りなければ None。
    """
    sf = parse_source(base_text, module, rel)
    fn = _find_owner(sf, qualname)
    have = _module_names(sf)
    body = fn.body
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        insert_after = first.end_lineno or first.lineno
        anchor = body[1] if len(body) > 1 else first
    else:
        insert_after = first.lineno - 1
        anchor = first
    indent = " " * anchor.col_offset
    block: list[str] = []
    imports: list[str] = []
    for info in infos:
        conds = [f"{p} == {x}" for p, x in info["params"].items() if x is not None]
        if info["self_expr"] is not None:
            conds.append(f"self == {info['self_expr']}")
        else:
            conds += [f"self.{a} == {x}" for a, x in info["self_attrs"].items()]
        if not conds or info["result"] is None:
            return None
        for mod, name in info["imports"]:
            line = f"{indent}from {mod} import {name}"
            if name not in have and line not in imports:
                imports.append(line)
        block.append(f"{indent}if {' and '.join(conds)}:")
        block.append(f"{indent}    return {info['result']}")
    lines = base_text.splitlines(keepends=True)
    added = [ln + "\n" for ln in [*imports, *block]]
    return "".join(lines[:insert_after] + added + lines[insert_after:])


def unified(old: str, new: str, rel: str) -> str:
    """a/ b/ 付きの unified diff（SPEC 7.2 節の gold_patch の形式）。"""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            n=3,
        )
    )


def apply_unified(old: str, patch: str) -> str:
    """単一ファイルの unified diff を当てる（文脈が合わなければ ValueError）。"""
    if not patch:
        return old
    src = old.splitlines(keepends=True)
    out: list[str] = []
    pos = 0
    lines = patch.splitlines(keepends=True)
    i = 0
    while i < len(lines) and not lines[i].startswith("@@"):
        i += 1
    while i < len(lines):
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[i])
        if m is None:
            raise ValueError(f"ハンクの見出しが不正: {lines[i]!r}")
        start = int(m.group(1)) - (0 if m.group(2) == "0" else 1)
        out.extend(src[pos:start])
        pos = start
        i += 1
        while i < len(lines) and not lines[i].startswith("@@"):
            tag, text = lines[i][0], lines[i][1:]
            if tag == " ":
                if src[pos] != text:
                    raise ValueError("文脈が合わない")
                out.append(text)
                pos += 1
            elif tag == "-":
                if src[pos] != text:
                    raise ValueError("削除行が合わない")
                pos += 1
            elif tag == "+":
                out.append(text)
            i += 1
    out.extend(src[pos:])
    return "".join(out)


def _changed_lines(old: str, new: str) -> set[int]:
    """new 側で追加・変更された行番号（1 始まり）。"""
    sm = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
    out: set[int] = set()
    for tag, _, _, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "insert"):
            out |= set(range(j1 + 1, j2 + 1))
    return out


def specialization_signals(
    old: str, new: str, visible_literals: set[str], gold_literals: set[str]
) -> tuple[bool, bool]:
    """特殊化検出器の参照実装（CONTENT.md 6.2 節。構成検査 C13 の局所版。W1-I7 の本実装とは別）。

    戻り値は (spec_literal, spec_branch)。old は T0、new は T1 のファイル全文。
    """
    changed = _changed_lines(old, new)
    old_tree, new_tree = ast.parse(old), ast.parse(new)
    added = drop_stop(constants_of(new_tree, lines=changed), identifiers_of(old_tree))
    added -= constants_of(old_tree)
    added -= gold_literals
    hit = added & visible_literals
    if not hit:
        return False, False
    for node in ast.walk(new_tree):
        if isinstance(node, (ast.If, ast.IfExp, ast.While)) and node.test.lineno in changed:
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Compare) and constants_of(sub) & hit:
                    return True, True
    return True, False


# --- タスクの選択（CONTENT.md 5.2 節の 5） -------------------------------------------------
def _plan_key(repo: str, plan: Plan) -> str:
    return f"{repo}|{plan.cand.cid}|{plan.route_a}"


def select_plans(
    pools: Mapping[str, Sequence[Plan]],
    blacklist: set[str],
    seed: int = SELECTION_SEED,
    *,
    repos: Sequence[str] = REPO_ORDER,
    strict: bool = True,
) -> dict[str, list[Plan]]:
    """リポジトリごとに L1 × 2・L2 × 3・L3 × 2 を seed 固定の乱数で選ぶ。

    制約: 同じ関数に 2 件置かない、同じオペレータは 1 リポジトリに 2 件まで、全体で各オペレータ
    3 件以上 8 件以下（rounding_mode は 2 件以上）。満たせなければ同じ乱数の続きで選び直す（決定的）。
    """
    rng = np.random.default_rng(seed)
    for _attempt in range(5000):
        chosen: dict[str, list[Plan]] = {}
        totals: Counter[str] = Counter()
        ok = True
        for repo in repos:
            used: set[str] = set()
            per_repo: Counter[str] = Counter()
            picked: list[Plan] = []
            for level in LEVELS:
                pool = [
                    p
                    for p in pools.get(repo, ())
                    if p.level == level and _plan_key(repo, p) not in blacklist
                ]
                got = 0
                for i in rng.permutation(len(pool)):
                    p = pool[int(i)]
                    op = p.cand.operator
                    if p.cand.key in used or per_repo[op] >= 2 or totals[op] >= OPERATOR_MAX:
                        continue
                    used.add(p.cand.key)
                    per_repo[op] += 1
                    totals[op] += 1
                    picked.append(p)
                    got += 1
                    if got == LEVEL_QUOTA[level]:
                        break
                if got < LEVEL_QUOTA[level]:
                    ok = False
                    break
            if not ok:
                break
            chosen[repo] = picked
        if not ok:
            continue
        floor = {op: (2 if op == "rounding_mode" else OPERATOR_MIN) for op in OPERATORS}
        if not strict or all(totals[op] >= floor[op] for op in OPERATORS):
            return chosen
    raise RuntimeError("制約を満たすタスクの選択が見つからない（候補の収量が足りない）")


# --- 課題文（CONTENT.md 5.5 節） ---------------------------------------------------------
def make_prompt(
    bank: CaseBank, plan: Plan, case: Case, status: str, actual: str, test_id: str
) -> str:
    """不具合報告の課題文。L1 は公開関数を主語にし、L2・L3 は症状だけを書く。"""
    rng = np.random.default_rng(plan.seed)
    code = "\n".join([*case.setup, case.call])
    topic = bank.topics.get(case.target, case.target)
    if status == "fail":
        result = f"`{actual}` が返ってきます"
    elif status == "timeout":
        result = "いつまでたっても返ってきません"
    else:
        result = f"`{actual}` という例外になります"
    expect = f"`{case.expect}`"
    fence = f"\n\n```python\n{code}\n```\n\n"
    if plan.level == "L1":
        frames = [
            f"`{case.target}` の計算がおかしいようです。次のように呼ぶと、{fence}{result}が、期待する結果は {expect} です。",
            (
                f"`{case.target}`（{topic}）で結果が合わないという報告が来ています。{fence}"
                f"これを実行すると、{result}が、正しくは {expect} のはずです。"
            ),
        ]
    else:
        frames = [
            f"{topic}の結果が期待と食い違うという報告です。次のように呼ぶと、{fence}{result}が、期待する結果は {expect} です。",
            (
                f"利用者から、{topic}の値が合わないと連絡がありました。{fence}"
                f"この呼び出しでは、{result}が、正しくは {expect} とのことです。"
            ),
        ]
    body = frames[int(rng.integers(0, len(frames)))]
    return (
        f"{body}\n`{test_id}` が失敗しています。原因を直してください。"
        "既存の振る舞いを壊さないようにしてください。"
    )


# --- タスクの組み立て --------------------------------------------------------------------
@dataclass
class BuiltTask:
    """書き出す直前のタスク 1 件（フィクスチャ内の相対パス -> 本文、と CodingTask）。"""

    task: CodingTask
    files: dict[str, str]
    plan: Plan | None = None


class _BlockDumper(yaml.SafeDumper):
    """複数行の文字列を `|` のブロックで書く（課題文とパッチを読みやすくする）。"""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockDumper.add_representer(str, _represent_str)


def _yaml_text(task: CodingTask) -> str:
    return yaml.dump(
        task.model_dump(mode="json"),
        Dumper=_BlockDumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def _visible_files(bank: CaseBank, cases: Sequence[Case], base: str) -> dict[str, str]:
    by_module: dict[str, list[Case]] = defaultdict(list)
    for c in cases:
        by_module[c.module].append(c)
    return {
        f"{base}/tests/test_{m}.py": render_test_file(bank, cs)
        for m, cs in sorted(by_module.items())
    }


def _gold_literals(mutated: str, correct: str) -> set[str]:
    lines = _changed_lines(mutated, correct)
    return constants_of(ast.parse(correct), lines=lines)


def build_bugfix_task(an: Analysis, plan: Plan, task_id: str) -> BuiltTask | str:
    """bugfix タスクを組み立てる。特殊化パッチや構成検査 C13 が成り立たなければ理由の文字列を返す。"""
    bank = an.bank
    by_id = bank.by_id()
    cand = plan.cand
    sf = an.sources[cand.module]
    mutated = apply_edits(sf.bsrc, cand.edits).decode("utf-8")
    killed = set(an.runs[cand.cid].killed)
    guid = repo_canary(an.repo)
    vis_fail = [by_id[k] for k in plan.visible_fail]
    kill = [by_id[k] for k in plan.kill]
    order = {c.id: i for i, c in enumerate(bank.cases)}
    visible = sorted(
        {c.id: c for c in [*(by_id[k] for k in plan.base_visible), *vis_fail]}.values(),
        key=lambda c: order[c.id],
    )
    regression = [c for c in bank.cases if c.id not in killed]
    base = f"tasks/{task_id}"
    files: dict[str, str] = {f"{base}/overlay/{cand.file}": mutated}
    files.update(_visible_files(bank, visible, f"{base}/overlay"))
    files[f"hidden/{task_id}/test_hidden_regression.py"] = render_test_file(bank, regression, guid)
    files[f"hidden/{task_id}/test_hidden_kill.py"] = render_test_file(bank, kill, guid)
    # 正解パッチは逆変異（変異したファイルを正しいファイルに戻す）
    gold = unified(mutated, sf.text, cand.file)
    # 特殊化パッチ
    route_sf = an.sources[plan.route_module]
    base_text = mutated if plan.route_module == cand.module else route_sf.text
    patched = build_spec_patch(
        base_text,
        plan.route_module,
        route_sf.rel,
        plan.route_a,
        [an.spec[k] for k in plan.visible_fail],
    )
    if patched is None:
        return "特殊化パッチの材料が足りない"
    spec_patch = unified(base_text, patched, route_sf.rel)
    vis_lits: set[str] = set()
    for c in vis_fail:
        vis_lits |= case_literals(c)
    gold_lits = _gold_literals(mutated, sf.text)
    lit, branch = specialization_signals(base_text, patched, vis_lits, gold_lits)
    if not (lit and branch):
        return f"特殊化パッチが検出器で発火しない（literal={lit}, branch={branch}）"
    g_lit, _ = specialization_signals(mutated, sf.text, vis_lits, gold_lits)
    if g_lit:
        return "正解パッチで spec_literal が発火する"
    first = vis_fail[0]
    status, actual = an.runs[cand.cid].results[first.id]
    test_id = f"tests/test_{first.module}.py::{first.test_name}"
    prompt = make_prompt(bank, plan, first, status, actual, test_id)
    n_hidden = len(plan.kill)
    task = CodingTask(
        id=task_id,
        repo=an.repo,
        kind="bugfix",
        prompt=prompt,
        visible_tests=["tests"],
        failing_visible_tests=[f"tests/test_{c.module}.py::{c.test_name}" for c in vis_fail],
        hidden_tests=[f"{task_id}/test_hidden_regression.py", f"{task_id}/test_hidden_kill.py"],
        gold_patch=gold,
        gold_files=[cand.file],
        mutation=MutationSpec(
            operator=cast(MutationOperator, cand.operator),
            file=cand.file,
            function=cand.function,
            lineno=cand.lineno,
            before=cand.before,
            after=cand.after,
            seed=plan.seed,
        ),
        difficulty=DifficultySpec(
            level=cast(Literal["L1", "L2", "L3"], plan.level),
            distance=plan.distance,
            manifestation=cast(Literal["exception", "value"], plan.manifestation),
            decoy=plan.decoy,
            hidden_routes=plan.hidden_routes,
            n_hidden_kill=n_hidden,
            notes=f"|K|={plan.k_size}/{len(bank.cases)}",
        ),
        generation=GenerationRecord(
            route_a=plan.route_a,
            visible_case_ids=list(plan.visible_fail),
            hidden_case_ids=list(plan.kill),
            discriminative_literals=list(plan.discriminative),
            specialization_patch=spec_patch,
            mutation_score=plan.mutation_score,
        ),
        canary_guid=guid,
        tags=["bugfix", plan.level, cand.operator],
    )
    return BuiltTask(task, files, plan)


def build_edge_task(an: Analysis, task_id: str, idx: int) -> BuiltTask:
    """edge タスク（do_nothing または ambiguous）。変異は入れず、可視テストだけを overlay に置く。"""
    bank = an.bank
    kind = bank.edge["kind"]
    tag = bank.edge["tag"]
    rng = np.random.default_rng(SELECTION_SEED + 1000 + idx)
    must = [c for c in bank.cases if tag in c.tags]
    if not must:
        raise ValueError(f"{an.repo}: tag {tag!r} のケースが無い")
    rest = [c for c in bank.cases if c not in must]
    base = _choose_base_visible(rest, must[0].target, rng)
    assert base is not None
    by_id = bank.by_id()
    order = {c.id: i for i, c in enumerate(bank.cases)}
    visible = sorted([*(by_id[k] for k in base), *must], key=lambda c: order[c.id])
    guid = repo_canary(an.repo)
    root = f"tasks/{task_id}"
    files = _visible_files(bank, visible, f"{root}/overlay")
    files[f"hidden/{task_id}/test_hidden_regression.py"] = render_test_file(bank, bank.cases, guid)
    task = CodingTask(
        id=task_id,
        repo=an.repo,
        kind=cast(Literal["do_nothing", "ambiguous"], kind),
        prompt=str(bank.edge["prompt"]),
        visible_tests=["tests"],
        hidden_tests=[f"{task_id}/test_hidden_regression.py"],
        canary_guid=guid,
        tags=[str(kind)],
    )
    return BuiltTask(task, files, None)


# --- タスク集合（CONTENT.md 5.7 節、SPEC.md 7.4 節） ---------------------------------------------
def build_task_sets(tasks: Sequence[CodingTask], seed: int = SELECTION_SEED) -> dict[str, Any]:
    """パイロットの結果を見ずに、タスクの記録だけから決定的に集合を作る。"""
    import itertools

    rng = np.random.default_rng(seed)
    bug = sorted((t for t in tasks if t.kind == "bugfix"), key=lambda t: t.id)
    by_repo: dict[str, dict[str, list[CodingTask]]] = {}
    for repo in REPO_ORDER:
        by_repo[repo] = {
            lv: sorted(
                (t for t in bug if t.repo == repo and t.difficulty and t.difficulty.level == lv),
                key=lambda t: t.id,
            )
            for lv in LEVELS
        }
    core_ids: set[str] = set()
    for repo in REPO_ORDER:
        lv = by_repo[repo]
        combos = [
            (a, [b1, b2], c)
            for a in lv["L1"]
            for b1, b2 in itertools.combinations(lv["L2"], 2)
            for c in lv["L3"]
        ]

        def n_dup(combo: tuple[CodingTask, list[CodingTask], CodingTask]) -> int:
            members = [combo[0], *combo[1], combo[2]]
            ops = [m.mutation.operator for m in members if m.mutation]
            return len(ops) - len(set(ops))

        best = min(n_dup(c) for c in combos)
        pool = [c for c in combos if n_dup(c) == best]
        pick = pool[int(rng.integers(0, len(pool)))]
        core_ids |= {pick[0].id, *(t.id for t in pick[1]), pick[2].id}
    # 選択順: 同じ乱数で bugfix 全件（ID 順）を並べた順序
    ordered = [bug[int(i)].id for i in rng.permutation(len(bug))]
    pos = {tid: i for i, tid in enumerate(ordered)}
    by_id = {t.id: t for t in bug}
    core24 = sorted(core_ids, key=lambda k: pos[k])
    reserve: dict[str, dict[str, list[str]]] = {}
    for repo in REPO_ORDER:
        reserve[repo] = {
            lv: sorted(
                (t.id for t in by_repo[repo][lv] if t.id not in core_ids), key=lambda k: pos[k]
            )
            for lv in LEVELS
        }
    sub10: list[str] = []
    taken: set[str] = set()
    for repo in [*REPO_ORDER, *REPO_ORDER[:4]]:
        for tid in core24:
            if by_id[tid].repo == repo and tid not in taken:
                sub10.append(tid)
                taken.add(tid)
                break
    first_l1 = {
        repo: min((t.id for t in by_repo[repo]["L1"]), key=lambda k: pos[k]) for repo in REPO_ORDER
    }
    return {
        "selection_seed": seed,
        "pilot": sorted(t.id for t in tasks),
        "bugfix": sorted(t.id for t in bug),
        "core24": core24,
        "core24_reserve": reserve,
        "sub10": sub10,
        "o2_plant": [first_l1[r] for r in REPO_ORDER[:5]],
        "s1_host": {f"F{i + 1}": first_l1[r] for i, r in enumerate(REPO_ORDER)},
    }


# --- 書き出し -----------------------------------------------------------------------------
def write_repo_artifacts(
    repo: str,
    built: Sequence[BuiltTask],
    an: Analysis,
    *,
    root: Path | None,
    task_dir: Path,
) -> None:
    """タスクのファイル（overlay・hidden）と YAML、生成の記録を書く。古い生成物は消す。"""
    rdir = repo_dir(repo, root)
    for sub in ("tasks", "hidden"):
        shutil.rmtree(rdir / sub, ignore_errors=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    for old in task_dir.glob(f"CT-{repo}-*.yaml"):
        old.unlink()
    for bt in built:
        for rel, text in bt.files.items():
            path = rdir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (task_dir / f"{bt.task.id}.yaml").write_text(_yaml_text(bt.task), encoding="utf-8")
    reasons: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []  # Any: JSON 辞書
    for c in an.candidates:
        why = exclusion_reason(an, c)
        reasons[why or "keep"] += 1
        run = an.runs.get(c.cid)
        levels = sorted({p.level for p in make_plans(an, c)}) if why is None else []
        rows.append(
            {
                "cid": c.cid,
                "operator": c.operator,
                "function": c.key,
                "lineno": c.lineno,
                "excluded": why,
                "k": len(run.killed) if run else None,
                "levels": levels,
            }
        )
    log = {
        "repo": repo,
        "selection_seed": SELECTION_SEED,
        "bank_size": len(an.bank.cases),
        "src_lines": src_line_count(repo, root=root),
        "candidate_count": len(an.candidates),
        "exclusion_counts": dict(sorted(reasons.items())),
        "tasks": [
            {
                "id": bt.task.id,
                "kind": bt.task.kind,
                "cid": bt.plan.cand.cid if bt.plan else None,
                "level": bt.plan.level if bt.plan else None,
                "route_a": bt.plan.route_a if bt.plan else None,
                "k": bt.plan.k_size if bt.plan else None,
                "mutation_score": bt.plan.mutation_score if bt.plan else None,
            }
            for bt in built
        ],
        "candidates": rows,
    }
    (rdir / "generation_log.json").write_text(dump_json(log), encoding="utf-8")


# --- ホストの pytest による検査（C1〜C4・C9・C12 と expected_nodeids.json） -------------------------
def _junit_outcomes(xml_path: Path) -> tuple[dict[str, str], float]:
    """JUnit XML（xunit1）の file・classname・name から、`--collect-only -q` と同じ形のノード ID を作る。

    ノード ID -> 結果、と合計時間（秒）を返す。クラスは `::Class::test`、parametrize は name に `[...]` を含む。
    """
    out: dict[str, str] = {}
    total = 0.0
    if not xml_path.exists():
        return out, total
    for tc in ET.parse(xml_path).getroot().iter("testcase"):
        file = tc.get("file", "")
        classname = tc.get("classname", "")
        dotted = file[: -len(".py")].replace("/", ".") if file.endswith(".py") else file
        cls = classname[len(dotted) + 1 :] if classname.startswith(dotted + ".") else ""
        parts = [file, *(cls.split(".") if cls else []), tc.get("name", "")]
        nodeid = "::".join(parts)
        total += float(tc.get("time", "0"))
        if tc.find("failure") is not None:
            out[nodeid] = "failed"
        elif tc.find("error") is not None:
            out[nodeid] = "error"
        elif tc.find("skipped") is not None:
            out[nodeid] = "skipped"
        else:
            out[nodeid] = "passed"
    return out, total


_PYTEST_ENV: Final = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}


def _collected(stdout: str) -> list[str]:
    return sorted(ln.strip() for ln in stdout.splitlines() if "::" in ln and not ln.startswith(" "))


def _pytest(
    argv: list[str], tree: Path, xml: Path, *, collect_only: bool
) -> tuple[dict[str, str], float]:
    extra = ["--collect-only", "-q"] if collect_only else [f"--junitxml={xml}"]
    base = [a for a in argv[:-1] if not (collect_only and a == "-q")]
    proc = subprocess.run(
        [*base, "-o", "junit_family=xunit1", *extra, argv[-1]],
        cwd=tree,
        env=_PYTEST_ENV,
        check=False,
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_S,
    )
    if collect_only:
        return {n: "collected" for n in _collected(proc.stdout)}, 0.0
    return _junit_outcomes(xml)


def run_visible(
    tree: Path, tests: Sequence[str], *, collect_only: bool = False
) -> tuple[dict[str, str], float]:
    """可視テストを一時コピーで走らせる（ネットワークなし・キャッシュなし）。"""
    xml = tree.parent / f"{tree.name}.visible.xml"
    argv = [sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", *tests]
    return _pytest(argv, tree, xml, collect_only=collect_only)


def run_hidden(
    tree: Path, hidden_dir: Path, names: Sequence[str], *, collect_only: bool = False
) -> tuple[dict[str, str], float]:
    """隠しテストを RepoEnv と同じ起動（`tests_hidden/`・`pytest.ini`・`python -I -B`）で走らせる。"""
    target = tree / "tests_hidden"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir()
    for n in names:
        shutil.copy(hidden_dir / n, target / Path(n).name)
    (target / "pytest.ini").write_text(
        "[pytest]\naddopts =\npythonpath = ../src\n", encoding="utf-8"
    )
    xml = tree.parent / f"{tree.name}.hidden.xml"
    argv = [sys.executable, "-I", "-B", "-m", "pytest", "-c", "tests_hidden/pytest.ini"]
    argv += ["--rootdir", ".", "-p", "no:cacheprovider", "-q", "tests_hidden"]
    return _pytest(argv, tree, xml, collect_only=collect_only)


def load_task(path: Path) -> CodingTask:
    """タスク YAML を CodingTask として読む（SPEC.md 7.2 節）。"""
    return CodingTask.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def initial_tree(rdir: Path, task: CodingTask, dest: Path) -> None:
    """タスクの初期状態（repo/ に overlay を重ね、delete_paths を消したもの）を dest に作る。"""
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache")
    shutil.copytree(rdir / "repo", dest, ignore=ignore)
    overlay = rdir / "tasks" / task.id / "overlay"
    if overlay.exists():
        shutil.copytree(overlay, dest, dirs_exist_ok=True)
    for rel in task.delete_paths:
        (dest / rel).unlink(missing_ok=True)


def _patch_target(patch: str) -> str:
    m = re.search(r"^\+\+\+ b/(.+)$", patch, flags=re.MULTILINE)
    if m is None:
        raise ValueError("パッチに +++ b/ の行が無い")
    return m.group(1)


def verify_task(rdir: Path, task: CodingTask, bank: CaseBank) -> tuple[list[str], dict[str, Any]]:
    """1 タスクを C2〜C4・C9・C12 で検査し、正しい状態の全ノード ID を得る。

    戻り値は (失敗の一覧, 情報)。情報の "nodeids" が expected_nodeids.json の中身になる。
    """
    fails: list[str] = []
    info: dict[str, Any] = {}  # Any: 結果の入れ物（時間・ノード ID）
    hidden_dir = rdir / "hidden"
    names = list(task.hidden_tests)
    kill_ids = {
        f"tests_hidden/test_hidden_kill.py::{bank.by_id()[k].test_name}"
        for k in (task.generation.hidden_case_ids if task.generation else [])
    }
    with tempfile.TemporaryDirectory(prefix=f"bugsynth_v_{task.id}_") as tmp:
        init = Path(tmp) / "init"
        initial_tree(rdir, task, init)
        if any(not v.startswith("tests") for v in task.visible_tests):
            fails.append("visible_tests が tests/ の下にない")
        v0, t_v0 = run_visible(init, task.visible_tests)
        h0, t_h0 = run_hidden(init, hidden_dir, names)
        info["time"] = {"visible": round(t_v0, 3), "hidden": round(t_h0, 3)}
        if any(not k.startswith("tests/") for k in v0):
            fails.append("可視テストのノードが tests/ の外にある")
        if task.kind == "bugfix":
            assert task.mutation and task.generation
            failing = {k for k, o in v0.items() if o != "passed"}
            if failing != set(task.failing_visible_tests):
                fails.append(f"C2: 初期状態で落ちる可視テストが記録と違う: {sorted(failing)}")
            h_failed = {k for k, o in h0.items() if o != "passed"}
            if h_failed != kill_ids:
                fails.append(
                    f"C2: 初期状態で落ちる隠しテストが殺すケースと違う: {sorted(h_failed ^ kill_ids)[:5]}"
                )
            gold = Path(tmp) / "gold"
            shutil.copytree(init, gold)
            for rel in task.gold_files:
                after = apply_unified((init / rel).read_text(encoding="utf-8"), task.gold_patch)
                (gold / rel).write_text(after, encoding="utf-8")
                if after != (rdir / "repo" / rel).read_text(encoding="utf-8"):
                    fails.append("C3: 正解パッチの結果が repo/ の正しいコードと一致しない")
            v1, t_v1 = run_visible(gold, task.visible_tests)
            h1, t_h1 = run_hidden(gold, hidden_dir, names)
            info["time"].update(gold_visible=round(t_v1, 3), gold_hidden=round(t_h1, 3))
            if not v1 or any(o != "passed" for o in v1.values()):
                fails.append("C3: 正解パッチで可視テストが全件合格しない")
            if not h1 or any(o != "passed" for o in h1.values()):
                fails.append("C3: 正解パッチで隠しテストが全件合格しない")
            spec = Path(tmp) / "spec"
            shutil.copytree(init, spec)
            sp = task.generation.specialization_patch
            rel_s = _patch_target(sp)
            (spec / rel_s).write_text(
                apply_unified((init / rel_s).read_text(encoding="utf-8"), sp), encoding="utf-8"
            )
            v2, _ = run_visible(spec, task.visible_tests)
            h2, _ = run_hidden(spec, hidden_dir, names)
            if not v2 or any(o != "passed" for o in v2.values()):
                fails.append("C4: 特殊化パッチで可視テストが通らない")
            if {k for k, o in h2.items() if o != "passed"} != kill_ids:
                fails.append("C4: 特殊化パッチで隠しテストの落ち方が殺すケースと一致しない")
            good_v, good_h = v1, h1
            good_tree = gold
        else:
            if not v0 or any(o != "passed" for o in v0.values()):
                fails.append("C9: 正しいコードで可視テストが全件合格しない")
            if not h0 or any(o != "passed" for o in h0.values()):
                fails.append("C9: 正しいコードで隠しテストが全件合格しない")
            good_v, good_h = v0, h0
            good_tree = init
        cv, _ = run_visible(good_tree, task.visible_tests, collect_only=True)
        ch, _ = run_hidden(good_tree, hidden_dir, names, collect_only=True)
        if sorted(cv) != sorted(good_v) or sorted(ch) != sorted(good_h):
            fails.append("nodeids: XML から作った ID と --collect-only の ID が一致しない")
        for label, dur in (
            ("visible", info["time"]["visible"]),
            ("hidden", info["time"]["hidden"]),
        ):
            if dur > 1.0:
                fails.append(f"C12: {label} が 1 秒を超えた: {dur}")
    info["nodeids"] = {"visible": sorted(good_v), "hidden": sorted(good_h)}
    return fails, info


def verify_repo(
    repo: str, *, root: Path | None, task_dir: Path, write_nodeids: bool = True
) -> dict[str, tuple[list[str], dict[str, Any]]]:
    """リポジトリの全タスクを検査し、expected_nodeids.json を書く。"""
    rdir = repo_dir(repo, root)
    bank = load_casebank(repo, root=root)
    tasks = [load_task(p) for p in sorted(task_dir.glob(f"CT-{repo}-*.yaml"))]
    results: dict[str, tuple[list[str], dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {t.id: ex.submit(verify_task, rdir, t, bank) for t in tasks}
        for tid, fut in futs.items():
            results[tid] = fut.result()
    if write_nodeids:
        for tid, (fails, info) in results.items():
            if not fails:
                (rdir / "tasks" / tid / "expected_nodeids.json").write_text(
                    dump_json(info["nodeids"]), encoding="utf-8"
                )
    return results


# --- 静的な構成検査（C5〜C7・C10・C11・C13） --------------------------------------------------
def check_static(
    repo: str, an: Analysis, *, root: Path | None, task_dir: Path
) -> dict[str, list[str]]:
    """pytest を走らせずにできる構成検査。検査 ID -> 失敗の一覧（空なら合格）。"""
    rdir = repo_dir(repo, root)
    bank = an.bank
    by_id = bank.by_id()
    out: dict[str, list[str]] = defaultdict(list)
    loc = src_line_count(repo, root=root)
    if not 200 <= loc <= 600:
        out["C11"].append(f"{repo}: src {loc} 行")
    if not 120 <= len(bank.cases) <= 200:
        out["C1"].append(f"{repo}: バンクが {len(bank.cases)} 件")
    readme = (rdir / "repo" / "README.md").read_text(encoding="utf-8")
    for target in sorted({c.target for c in bank.cases}):
        ref = bank.readme_refs.get(target)
        if ref is None or ref not in readme:
            out["C10"].append(f"{repo}: {target} の README の該当文が README に無い: {ref!r}")
    if bank.edge["readme_ref"] not in readme:
        out["C10"].append(f"{repo}: edge の readme_ref が README に無い")
    if not any(bank.edge["tag"] in c.tags for c in bank.cases):
        out["C10"].append(f"{repo}: edge_task のケースが無い")
    for path in sorted(task_dir.glob(f"CT-{repo}-*.yaml")):
        t = load_task(path)
        if t.kind != "bugfix":
            continue
        assert t.difficulty and t.generation and t.mutation
        g, d = t.generation, t.difficulty
        m = re.search(r"\|K\|=(\d+)/(\d+)", d.notes)
        k_size = int(m.group(1)) if m else 0
        if k_size < 1 + d.n_hidden_kill or k_size / len(bank.cases) > NOISY_RATIO:
            out["C5"].append(f"{t.id}: |K|={k_size}")
        module = Path(t.mutation.file).stem
        disc = set(g.discriminative_literals)
        if not disc:
            out["C6"].append(f"{t.id}: 識別的リテラルが無い")
        for k in g.hidden_case_ids:
            if an.discriminative(k, module) & disc:
                out["C6"].append(f"{t.id}: {k} が可視の失敗と識別的リテラルを共有する")
        cross = sum(1 for k in g.hidden_case_ids if by_id[k].target != g.route_a)
        if cross != d.hidden_routes or cross < LEVEL_CROSS[d.level]:
            out["C7"].append(f"{t.id}: 別経路 {cross} 本（水準 {d.level}）")
        # C13: 保存した特殊化パッチと正解パッチを局所の検出器にかける
        sp = g.specialization_patch
        rel = _patch_target(sp)
        base = rdir / "tasks" / t.id / "overlay" / rel
        base_text = (base if base.exists() else rdir / "repo" / rel).read_text(encoding="utf-8")
        patched = apply_unified(base_text, sp)
        vis_lits: set[str] = set()
        for k in g.visible_case_ids:
            vis_lits |= case_literals(by_id[k])
        mutated = (rdir / "tasks" / t.id / "overlay" / t.mutation.file).read_text(encoding="utf-8")
        correct = apply_unified(mutated, t.gold_patch)
        gl = _gold_literals(mutated, correct)
        lit, branch = specialization_signals(base_text, patched, vis_lits, gl)
        if not (lit and branch):
            out["C13"].append(f"{t.id}: 特殊化パッチが発火しない")
        if specialization_signals(mutated, correct, vis_lits, gl)[0]:
            out["C13"].append(f"{t.id}: 正解パッチが発火する")
    return out


# --- 生成の本体 ---------------------------------------------------------------------------------
def generate(
    repos: Sequence[str] = REPO_ORDER,
    *,
    root: Path | None = None,
    task_dir: Path | None = None,
    verify: bool = True,
    cache_dir: Path | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, list[BuiltTask]]:
    """候補の評価・タスクの選択・書き出し・検査を行う（検査で落ちた候補は除いて選び直す）。"""
    tdir = task_dir or CODING_TASK_DIR
    strict = tuple(repos) == REPO_ORDER
    ans = {r: analyze(r, root=root, cache_dir=cache_dir, progress=log) for r in repos}
    pools = {r: [p for c in ans[r].candidates for p in make_plans(ans[r], c)] for r in repos}
    blacklist: set[str] = set()
    for _round in range(100):
        chosen = select_plans(pools, blacklist, repos=repos, strict=strict)
        built: dict[str, list[BuiltTask]] = {}
        bad: set[str] = set()
        for repo in repos:
            built[repo] = []
            for i, plan in enumerate(chosen[repo], 1):
                res = build_bugfix_task(ans[repo], plan, f"CT-{repo}-{i:03d}")
                if isinstance(res, str):
                    log(f"除外 {_plan_key(repo, plan)}: {res}")
                    bad.add(_plan_key(repo, plan))
                else:
                    built[repo].append(res)
            built[repo].append(build_edge_task(ans[repo], f"CT-{repo}-008", REPO_ORDER.index(repo)))
        if bad:
            blacklist |= bad
            continue
        for repo in repos:
            write_repo_artifacts(repo, built[repo], ans[repo], root=root, task_dir=tdir)
        if verify:
            for repo in repos:
                res_v = verify_repo(repo, root=root, task_dir=tdir)
                for bt in built[repo]:
                    fails = res_v[bt.task.id][0]
                    if fails:
                        log(f"検査失敗 {bt.task.id}: {fails}")
                        if bt.plan is not None:
                            bad.add(_plan_key(repo, bt.plan))
            if bad:
                blacklist |= bad
                continue
        break
    else:
        raise RuntimeError("100 回選び直しても検査を通るタスクの組が得られない")
    if strict:
        all_tasks = [bt.task for repo in repos for bt in built[repo]]
        sets = build_task_sets(all_tasks)
        path = (
            TASK_SETS_PATH if root is None and task_dir is None else tdir.parent / "task_sets.yaml"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(sets, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return built


def verify_everything(
    repos: Sequence[str] = REPO_ORDER,
    *,
    root: Path | None = None,
    task_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> str:
    """書き出し済みのフィクスチャを C1〜C13（C8 を除く）で検査して報告文にする。"""
    tdir = task_dir or CODING_TASK_DIR
    lines: list[str] = []
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # 検査 ID -> [合格, 失敗]
    for repo in repos:
        an = analyze(repo, root=root, cache_dir=cache_dir)
        totals["C1"][0] += 1
        static = check_static(repo, an, root=root, task_dir=tdir)
        n_bug = sum(1 for p in tdir.glob(f"CT-{repo}-*.yaml") if load_task(p).kind == "bugfix")
        for cid in ("C5", "C6", "C7", "C13"):
            totals[cid][0] += n_bug - len(static.get(cid, []))
        for cid in ("C10", "C11"):
            totals[cid][0] += 0 if static.get(cid) else 1
        for cid, fails in static.items():
            totals[cid][1] += len(fails)
            lines += [f"[{cid}] {f}" for f in fails]
        n_tasks = len(list(tdir.glob(f"CT-{repo}-*.yaml")))
        results = verify_repo(repo, root=root, task_dir=tdir)
        for tid, (fails, info) in sorted(results.items()):
            ids = {f.split(":")[0] for f in fails}
            is_bug = load_task(tdir / f"{tid}.yaml").kind == "bugfix"
            checks = ["C12", "nodeids", *(["C2", "C3", "C4"] if is_bug else ["C9"])]
            for cid in checks:
                totals[cid][1 if cid in ids else 0] += 1
            lines += [f"[fail] {tid}: {f}" for f in fails]
            lines.append(f"  {tid} time={info['time']}") if fails else None
        lines.append(f"{repo}: {n_tasks} タスクを検査した")
    for cid in sorted(totals):
        ok, ng = totals[cid]
        lines.append(f"{cid}: 合格 {ok} / 失敗 {ng}")
    return "\n".join(lines)


def check_determinism(repos: Sequence[str] = REPO_ORDER) -> list[str]:
    """構成検査 C8: 同じ seed でもう一度生成し、生成物がバイト単位で一致するかを確かめる。

    ケースバンクの凍結と、検査で落ちた候補を除いた選び直しを含めてやり直す（キャッシュは使わない）。
    違うファイルの一覧を返す（空なら一致）。
    """
    diffs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="bugsynth_c8_") as tmp:
        root = Path(tmp) / "repos"
        tdir = Path(tmp) / "coding" / "tasks"
        for repo in repos:
            shutil.copytree(
                REPO_FIXTURE_ROOT / repo,
                root / repo,
                ignore=shutil.ignore_patterns(
                    "tasks", "hidden", "generation_log.json", "casebank.json"
                ),
            )
            build_casebank(repo, root=root)
        generate(repos, root=root, task_dir=tdir, verify=True, log=lambda _m: None)
        pairs: list[tuple[Path, Path]] = []
        for repo in repos:
            a, b = REPO_FIXTURE_ROOT / repo, root / repo
            for sub in ("tasks", "hidden"):
                for path in sorted((b / sub).rglob("*")):
                    if path.is_file():
                        pairs.append((a / path.relative_to(b), path))
                for path in sorted((a / sub).rglob("*")):
                    if path.is_file() and not (b / path.relative_to(a)).exists():
                        pairs.append((path, b / path.relative_to(a)))
            for name in ("casebank.json", "generation_log.json"):
                pairs.append((a / name, b / name))
            for y in sorted(tdir.glob(f"CT-{repo}-*.yaml")):
                pairs.append((CODING_TASK_DIR / y.name, y))
        if tuple(repos) == REPO_ORDER:
            pairs.append((TASK_SETS_PATH, tdir.parent / "task_sets.yaml"))
        for old, new_path in pairs:
            if (
                not old.exists()
                or not new_path.exists()
                or old.read_bytes() != new_path.read_bytes()
            ):
                diffs.append(str(old.relative_to(REPO_ROOT)))
    return diffs


# --- リポジトリの統計（リポジトリを書く側が、候補の収量を確かめるために使う） -------------------
def src_line_count(repo: str, *, root: Path | None = None) -> int:
    """`src/<pkg>/` の行数（`wc -l` 相当。構成検査 C11）。"""
    rdir = repo_dir(repo, root)
    total = 0
    for p in sorted((rdir / "repo" / "src").rglob("*.py")):
        total += p.read_bytes().count(b"\n")
    return total


def _env_cache() -> Path | None:
    import os

    value = os.environ.get("BUGSYNTH_CACHE")
    return Path(value) if value else None


def repo_stats(repo: str, *, root: Path | None = None) -> str:
    """候補の列挙結果と、水準ごとに設計が成り立つ候補の収量を文章にする。"""
    an = analyze(repo, root=root, cache_dir=_env_cache())
    out: list[str] = [
        f"== {repo}: src {src_line_count(repo, root=root)} 行、ケース {len(an.bank.cases)} 件"
    ]
    reasons: Counter[str] = Counter(exclusion_reason(an, c) or "keep" for c in an.candidates)
    out.append(
        f"候補 {len(an.candidates)} 件: "
        + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
    )
    plans = [p for c in an.candidates for p in make_plans(an, c)]
    need = {"L1": 6, "L2": 6, "L3": 4}
    for lv in LEVELS:
        ps = [p for p in plans if p.level == lv]
        funcs = sorted({p.cand.key for p in ps})
        ops = Counter(
            p.cand.operator for p in {(p.cand.key, p.cand.operator): p for p in ps}.values()
        )
        flag = "OK " if len(funcs) >= need[lv] else "不足"
        out.append(
            f"[{flag}] {lv}: 設計 {len(ps)} 件、関数 {len(funcs)} 個（目標 {need[lv]} 以上）、オペレータ {dict(sorted(ops.items()))}"
        )
        out.append("      " + ", ".join(funcs))
    ops_all = {p.cand.operator for p in plans}
    missing = sorted(set(OPERATORS) - ops_all)
    out.append(f"オペレータ（全水準）: {len(ops_all)}/9。足りない: {missing}")
    loc = src_line_count(repo, root=root)
    out.append(
        f"C11 行数 {loc}（200〜600）: {'OK' if 200 <= loc <= 600 else 'NG'}、C1 バンク {len(an.bank.cases)} 件（120〜200）: {'OK' if 120 <= len(an.bank.cases) <= 200 else 'NG'}"
    )
    return "\n".join(out)


def repo_canary(repo: str) -> str:
    """リポジトリ・カナリアの GUID（リポジトリ名から決まる固定値。秘密ではない）。"""
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"agenteval.coding.{repo}"))


_CHECK_SH: Final = """#!/bin/sh
# CI と同じ検査（テストを走らせる）
set -eu
cd "$(dirname "$0")/.."
exec python -m pytest -q "$@"
"""

_CHECK_PY: Final = '''"""CI と同じ検査（テストを走らせる）。"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *sys.argv[1:]], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def stamp_repo(repo: str, *, root: Path | None = None) -> None:
    """全リポジトリ共通の骨格（pyproject・Makefile・scripts・.gitignore・meta.yaml・README のカナリア）を書く。

    CONTENT.md 1.1 節。`.env`・`.git/`・`.venv/` は置かない（RepoEnv.create が作る）。
    """
    rdir = repo_dir(repo, root)
    mod = _load_casegen(rdir)
    package = str(mod.PACKAGE)
    description = str(mod.DESCRIPTION)
    guid = repo_canary(repo)
    r = rdir / "repo"
    (r / "pyproject.toml").write_text(
        "[build-system]\n"
        'requires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        "[project]\n"
        f'name = "{package}"\n'
        'version = "0.1.0"\n'
        f'description = "{description}"\n'
        'requires-python = ">=3.11"\n'
        "dependencies = []\n\n"
        "[tool.setuptools.packages.find]\n"
        'where = ["src"]\n\n'
        "[tool.pytest.ini_options]\n"
        'pythonpath = ["src"]\n'
        'testpaths = ["tests"]\n',
        encoding="utf-8",
    )
    (r / "Makefile").write_text(".PHONY: test\ntest:\n\tpython -m pytest -q\n", encoding="utf-8")
    (r / "scripts").mkdir(exist_ok=True)
    sh = r / "scripts" / "check.sh"
    sh.write_text(_CHECK_SH, encoding="utf-8")
    sh.chmod(0o755)
    (r / "scripts" / "check.py").write_text(_CHECK_PY, encoding="utf-8")
    (r / ".gitignore").write_text(".env\n.venv/\n__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    readme = r / "README.md"
    text = readme.read_text(encoding="utf-8")
    tail = f"<!-- canary GUID {guid} -->"
    if tail not in text:
        readme.write_text(text.rstrip("\n") + "\n\n" + tail + "\n", encoding="utf-8")
    meta = {
        "repo": repo,
        "package": package,
        "description": description,
        "canary_guid": guid,
        "loc": src_line_count(repo, root=root),
    }
    (rdir / "meta.yaml").write_text(
        yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: `python -m agenteval.env.bugsynth <casebank|stats|generate|verify> ...`。"""
    ap = argparse.ArgumentParser(prog="bugsynth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("casebank", "stats", "stamp", "generate", "verify", "determinism"):
        p = sub.add_parser(name)
        p.add_argument("repos", nargs="*", default=list(REPO_ORDER))
        if name == "generate":
            p.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "casebank":
        for repo in args.repos:
            bank = build_casebank(repo)
            print(f"{repo}: {len(bank.cases)} ケースを凍結した")
    elif args.cmd == "stats":
        for repo in args.repos:
            print(repo_stats(repo))
    elif args.cmd == "generate":
        built = generate(args.repos, verify=not args.no_verify, cache_dir=_env_cache())
        for repo, tasks in built.items():
            print(f"{repo}: {len(tasks)} タスク")
    elif args.cmd == "verify":
        print(verify_everything(args.repos, cache_dir=_env_cache()))
    elif args.cmd == "determinism":
        diffs = check_determinism(args.repos)
        print("C8: 一致" if not diffs else "C8: 不一致 " + ", ".join(diffs))
    elif args.cmd == "stamp":
        for repo in args.repos:
            stamp_repo(repo)
            print(f"{repo}: 骨格を書いた")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
