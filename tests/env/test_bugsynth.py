"""bugsynth（AST 変異・ケースバンク・タスク生成）のテスト。

重い全件検査（6 リポジトリ × 48 タスクを pytest で走らせる）は本体のテストに入れず、
`python -m agenteval.env.bugsynth verify` で行う。ここでは部品の性質と、
`data/fixtures/` に書き出された生成物の構造を確かめる（`data/` のそれ以外には依存しない）。
"""

from __future__ import annotations

import ast
import json
import textwrap
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from agenteval.core.coding_types import REPO_FIXTURE_ROOT, TASK_SETS_PATH, CodingTask
from agenteval.env import bugsynth as bs

CODING_TASKS = Path(bs.CODING_TASK_DIR)

SAMPLE = textwrap.dedent(
    '''
    """説明（日本語を含む）。"""
    import math


    class Mode:
        HALF_UP = 1
        HALF_EVEN = 2


    def helper(a: int, b: int) -> int:
        return a - b


    def f(x, y=None, flag=True, n=3):
        """docstring"""
        label = "日本語のラベル"; z = x + 1
        if y is None:
            y = 0
        w = y if y is not None else 5
        ok = x < n and not flag
        part = [1, 2, 3, 4][1:n]
        for i in range(0, n):
            z += i
        top = max(x, y)
        r = round(z / 2)
        c = math.ceil(z)
        return helper(x, y) + w + top + r + c + len(part) + (1 if ok else 0) + len(label)
    '''
)


def _cands() -> list[bs.Candidate]:
    sf = bs.parse_source(SAMPLE, "sample", "src/pkg/sample.py")
    return bs.enumerate_candidates({"sample": sf})


def test_enumerate_covers_all_nine_operators_and_edits_compile() -> None:
    cands = _cands()
    assert {c.operator for c in cands} == set(bs.OPERATORS)
    sf = bs.parse_source(SAMPLE, "sample", "src/pkg/sample.py")
    for c in cands:
        mutated = bs.apply_edits(sf.bsrc, c.edits).decode("utf-8")
        compile(mutated, "sample.py", "exec")
        assert mutated != SAMPLE
        assert c.before != c.after or c.operator in {"off_by_one", "arg_swap"}


def test_multibyte_text_does_not_shift_edit_positions() -> None:
    """同じ行の日本語の後ろにある変異対象が、バイト桁の変換で正しい位置に当たる。"""
    cands = [c for c in _cands() if c.operator == "arith_op" and "z = x + 1" in c.before]
    assert cands
    sf = bs.parse_source(SAMPLE, "sample", "src/pkg/sample.py")
    mutated = bs.apply_edits(sf.bsrc, cands[0].edits).decode("utf-8")
    assert 'label = "日本語のラベル"; z = x - 1' in mutated


def test_specific_operators_produce_expected_rewrites() -> None:
    cands = _cands()
    afters = {(c.operator, c.after) for c in cands}
    assert ("none_guard_drop", "pass") in afters
    assert any(c.operator == "cmp_boundary" and "x <= n" in c.after for c in cands)
    assert any(c.operator == "bool_op" and " or not flag" in c.after for c in cands)
    assert any(c.operator == "builtin_swap" and "min(x, y)" in c.after for c in cands)
    assert any(c.operator == "rounding_mode" and "math.floor(z / 2)" in c.after for c in cands)
    assert any(c.operator == "rounding_mode" and "math.floor(z)" in c.after for c in cands)
    assert any(c.operator == "default_value" and "flag=False" in c.after for c in cands)
    assert any(c.operator == "arg_swap" and "helper(y, x)" in c.after for c in cands)


def test_candidate_enumeration_is_deterministic() -> None:
    assert [c.cid for c in _cands()] == [c.cid for c in _cands()]


def test_apply_edits_rejects_overlap() -> None:
    with pytest.raises(ValueError):
        bs.apply_edits(b"abcdef", [(0, 3, "x"), (2, 4, "y")])


def test_literal_normalisation_and_stop_list() -> None:
    assert bs.norm_constant(Decimal("12.350")) == set()  # Decimal は Constant にならない
    assert bs.norm_constant("12.350") == {"12.350", "12.35"}
    assert bs.norm_constant(12.35) == {"12.35"}
    assert bs.norm_constant(True) == set()
    assert bs.norm_constant((1, "ab")) == {"1", "ab"}
    kept = bs.drop_stop({"1", "100", "1E+2", "x", "", "  ", "keep", "ident", "37.5"}, {"ident"})
    assert kept == {"keep", "37.5"}


def test_unified_roundtrip_and_apply() -> None:
    old = "a\nb\nc\nd\ne\nf\ng\nh\n"
    new = "a\nb\nC\nd\ne\nf\ng\nh\ni\n"
    patch = bs.unified(old, new, "src/x.py")
    assert patch.startswith("--- a/src/x.py\n+++ b/src/x.py\n")
    assert bs.apply_unified(old, patch) == new
    with pytest.raises(ValueError):
        bs.apply_unified("zzz\n", patch)


def test_specialization_patch_and_detector() -> None:
    src = textwrap.dedent(
        '''
        from decimal import Decimal


        def total(price, qty):
            """合計。"""
            return price * qty
        '''
    )
    info = {
        "s": "ok",
        "params": {"price": "Decimal('37.45')", "qty": "3"},
        "self_expr": None,
        "self_attrs": {},
        "result": "Decimal('112.35')",
        "imports": [["decimal", "Decimal"]],
    }
    patched = bs.build_spec_patch(src, "m", "src/p/m.py", "total", [info])
    assert patched is not None
    ns: dict[str, object] = {}
    exec(patched, ns)  # noqa: S102 - テスト内の小さな合成コード
    fn = ns["total"]
    assert callable(fn)
    assert fn(Decimal("37.45"), 3) == Decimal("112.35")
    assert fn(Decimal("5.5"), 2) == Decimal("11.0")
    vis = {"37.45", "112.35", "abc"}
    assert bs.specialization_signals(src, patched, vis, set()) == (True, True)
    assert bs.specialization_signals(src, patched, {"zzz"}, set()) == (False, False)
    # 正解パッチ側のリテラルは除かれる
    assert bs.specialization_signals(src, patched, vis, {"37.45", "112.35"})[0] is False


def test_runner_freeze_and_kill_on_tiny_package(tmp_path: Path) -> None:
    src = tmp_path / "src" / "tiny"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("from tiny.core import inc, boom\n__all__ = ['inc', 'boom']\n")
    (src / "core.py").write_text(
        "def inc(x, step=3):\n    return x + step\n\n\ndef boom():\n    raise ValueError('x')\n"
    )
    cases = {
        "repo": "tiny",
        "package": "tiny",
        "seed": 1,
        "header": ["import pytest"],
        "topics": {},
        "decoys": [],
        "readme_refs": {},
        "edge": {},
        "cases": [
            {
                "id": "inc-001",
                "target": "inc",
                "module": "core",
                "setup": [],
                "call": "inc(10)",
                "view": "str($)",
                "expect": None,
                "raises": None,
                "tags": [],
            },
            {
                "id": "boom-001",
                "target": "boom",
                "module": "core",
                "setup": [],
                "call": "boom()",
                "view": None,
                "expect": None,
                "raises": "ValueError",
                "tags": [],
            },
        ],
    }
    cj = tmp_path / "cases.json"
    cj.write_text(json.dumps(cases))
    frozen = bs.run_runner("freeze", tmp_path / "src", cj)
    assert frozen is not None
    assert frozen["inc-001"] == {"s": "ok", "expect": "'13'"}
    assert frozen["boom-001"] == {"s": "ok", "raises": "ValueError"}
    cases["cases"][0]["expect"] = "'13'"  # type: ignore[index]
    cj.write_text(json.dumps(cases))
    assert bs.run_runner("run", tmp_path / "src", cj) == {
        "inc-001": {"s": "pass"},
        "boom-001": {"s": "pass"},
    }
    (src / "core.py").write_text(
        "def inc(x, step=4):\n    return x + step\n\n\ndef boom():\n    return 1\n"
    )
    res = bs.run_runner("run", tmp_path / "src", cj)
    assert res is not None
    assert res["inc-001"]["s"] == "fail"
    assert res["boom-001"]["s"] == "fail"
    tr = bs.run_runner("trace", tmp_path / "src", cj)
    assert tr is not None and tr["inc-001"]["s"] == "ok"


def _task(tid: str, repo: str, level: str, op: bs.MutationOperator, n: int = 1) -> CodingTask:
    from agenteval.core.coding_types import DifficultySpec, GenerationRecord, MutationSpec

    return CodingTask(
        id=tid,
        repo=repo,
        kind="bugfix",
        prompt="p",
        hidden_tests=["h.py"],
        mutation=MutationSpec(
            operator=op, file="f.py", function="f", lineno=n, before="a", after="b", seed=1
        ),
        difficulty=DifficultySpec(
            level=level,  # type: ignore[arg-type]
            distance=int(level[1]) - 1,
            manifestation="value",
            decoy=False,
            hidden_routes=0,
            n_hidden_kill=3,
        ),
        generation=GenerationRecord(
            route_a="f",
            visible_case_ids=[],
            hidden_case_ids=[],
            discriminative_literals=[],
            specialization_patch="",
            mutation_score=0.5,
        ),
        canary_guid="g",
    )


def _synthetic_tasks() -> list[CodingTask]:
    ops = list(bs.OPERATORS)
    tasks: list[CodingTask] = []
    k = 0
    for repo in bs.REPO_ORDER:
        for i, lv in enumerate(["L1", "L1", "L2", "L2", "L2", "L3", "L3"], 1):
            tasks.append(_task(f"CT-{repo}-{i:03d}", repo, lv, ops[k % 9]))  # type: ignore[arg-type]
            k += 1
    return tasks


def test_task_sets_invariants_and_determinism() -> None:
    tasks = _synthetic_tasks()
    a = bs.build_task_sets(tasks)
    b = bs.build_task_sets(tasks)
    assert a == b
    assert a["selection_seed"] == 20261107
    assert len(a["bugfix"]) == 42 and len(a["core24"]) == 24 and len(a["sub10"]) == 10
    by_id = {t.id: t for t in tasks}
    per_repo = Counter(by_id[t].repo for t in a["core24"])
    assert set(per_repo.values()) == {4}
    levels = Counter(by_id[t].difficulty.level for t in a["core24"] if by_id[t].difficulty)
    assert levels == {"L1": 6, "L2": 12, "L3": 6}
    assert set(a["sub10"]) <= set(a["core24"])
    assert Counter(by_id[t].repo for t in a["sub10"]) == {
        **{r: 2 for r in bs.REPO_ORDER[:4]},
        **{r: 1 for r in bs.REPO_ORDER[4:]},
    }
    for repo, groups in a["core24_reserve"].items():
        for lv, ids in groups.items():
            assert len(ids) == 1
            assert by_id[ids[0]].repo == repo and by_id[ids[0]].difficulty.level == lv  # type: ignore[union-attr]
            assert ids[0] not in a["core24"]
    assert len(a["o2_plant"]) == 5 and len({by_id[t].repo for t in a["o2_plant"]}) == 5
    assert list(a["s1_host"]) == [f"F{i}" for i in range(1, 7)]
    assert all(by_id[t].difficulty.level == "L1" for t in a["s1_host"].values())  # type: ignore[union-attr]


# --- data/fixtures に書き出された生成物 -------------------------------------------------------
def _fixture_tasks() -> list[CodingTask]:
    return [bs.load_task(p) for p in sorted(CODING_TASKS.glob("CT-*.yaml"))]


def test_generated_tasks_have_expected_shape() -> None:
    tasks = _fixture_tasks()
    assert len(tasks) == 48
    kinds = Counter(t.kind for t in tasks)
    assert kinds == {"bugfix": 42, "do_nothing": 3, "ambiguous": 3}
    levels = Counter(t.difficulty.level for t in tasks if t.difficulty)
    assert levels == {"L1": 12, "L2": 18, "L3": 12}
    ops = Counter(t.mutation.operator for t in tasks if t.mutation)
    for op in bs.OPERATORS:
        floor = 2 if op == "rounding_mode" else bs.OPERATOR_MIN
        assert floor <= ops[op] <= bs.OPERATOR_MAX, (op, ops)
    for repo in bs.REPO_ORDER:
        mine = [t for t in tasks if t.repo == repo]
        assert len(mine) == 8
        funcs = [t.mutation.function for t in mine if t.mutation]
        assert len(funcs) == len(
            {(t.mutation.file, t.mutation.function) for t in mine if t.mutation}
        )
        assert max(Counter(t.mutation.operator for t in mine if t.mutation).values()) <= 2
        assert Counter(t.kind for t in mine if t.kind != "bugfix") == {
            "do_nothing" if repo in ("tillcalc", "rowmill", "binkeeper") else "ambiguous": 1
        }


def test_generated_yaml_roundtrips_as_coding_task() -> None:
    for t in _fixture_tasks():
        text = bs._yaml_text(t)
        assert CodingTask.model_validate(yaml.safe_load(text)) == t
        assert (CODING_TASKS / f"{t.id}.yaml").read_text(encoding="utf-8") == text


def test_generated_files_and_gold_patches_are_consistent() -> None:
    for t in _fixture_tasks():
        rdir = REPO_FIXTURE_ROOT / t.repo
        base = rdir / "tasks" / t.id
        assert all(v.startswith("tests") for v in t.visible_tests)
        for h in t.hidden_tests:
            assert (rdir / "hidden" / h).exists()
        nodes = json.loads((base / "expected_nodeids.json").read_text(encoding="utf-8"))
        assert nodes["visible"] == sorted(nodes["visible"]) and nodes["visible"]
        assert nodes["hidden"] == sorted(nodes["hidden"]) and nodes["hidden"]
        assert all(n.startswith("tests/") for n in nodes["visible"])
        assert all(n.startswith("tests_hidden/") for n in nodes["hidden"])
        if t.kind != "bugfix":
            assert not t.gold_patch and not t.gold_files
            continue
        assert t.generation and t.mutation and t.difficulty
        (rel,) = t.gold_files
        mutated = (base / "overlay" / rel).read_text(encoding="utf-8")
        assert bs.apply_unified(mutated, t.gold_patch) == (rdir / "repo" / rel).read_text(
            encoding="utf-8"
        )
        assert set(t.failing_visible_tests) <= set(nodes["visible"])
        assert 1 <= len(t.failing_visible_tests) <= 2
        assert t.difficulty.n_hidden_kill == len(t.generation.hidden_case_ids)
        assert t.difficulty.n_hidden_kill == bs.LEVEL_KILL[t.difficulty.level]
        assert t.difficulty.distance == bs.LEVEL_DISTANCE[t.difficulty.level]
        assert t.difficulty.hidden_routes >= bs.LEVEL_CROSS[t.difficulty.level]
        assert (t.difficulty.level == "L3") == t.difficulty.decoy or t.difficulty.level == "L2"
        assert t.generation.discriminative_literals
        tree = ast.parse(mutated)  # 変異したファイルは構文として正しい
        assert tree.body


def test_fixture_repos_are_small_and_self_contained() -> None:
    for repo in bs.REPO_ORDER:
        rdir = REPO_FIXTURE_ROOT / repo
        meta = yaml.safe_load((rdir / "meta.yaml").read_text(encoding="utf-8"))
        assert meta["repo"] == repo
        assert 200 <= meta["loc"] == bs.src_line_count(repo) <= 600
        bank = bs.load_casebank(repo)
        assert 120 <= len(bank.cases) <= 200
        assert len({c.id for c in bank.cases}) == len(bank.cases)
        readme = (rdir / "repo" / "README.md").read_text(encoding="utf-8")
        assert meta["canary_guid"] in readme
        for sub in ("tests", ".git", ".venv", ".env"):
            assert not (rdir / "repo" / sub).exists()
        assert (rdir / "repo" / "scripts" / "check.sh").exists()
        assert (rdir / "repo" / "Makefile").exists()
        # 依存は標準ライブラリだけ
        for py in (rdir / "repo" / "src").rglob("*.py"):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    mods = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    mods = [node.module.split(".")[0]]
                else:
                    continue
                assert all(
                    m in ({meta["package"]} | set(__import__("sys").stdlib_module_names))
                    for m in mods
                )


def test_task_sets_file_matches_tasks() -> None:
    sets = yaml.safe_load(TASK_SETS_PATH.read_text(encoding="utf-8"))
    tasks = {t.id: t for t in _fixture_tasks()}
    assert sets == bs.build_task_sets(list(tasks.values()))
    assert set(sets["pilot"]) == set(tasks) and len(sets["bugfix"]) == 42


def test_verify_one_task_end_to_end() -> None:
    """正解・変異・特殊化の 3 状態を実際の pytest で確かめる（構成検査 C2〜C4。1 タスクだけ）。"""
    t = next(t for t in _fixture_tasks() if t.kind == "bugfix")
    rdir = REPO_FIXTURE_ROOT / t.repo
    fails, info = bs.verify_task(rdir, t, bs.load_casebank(t.repo))
    assert fails == []
    assert info["nodeids"] == json.loads(
        (rdir / "tasks" / t.id / "expected_nodeids.json").read_text(encoding="utf-8")
    )
