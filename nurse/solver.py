"""求解と解の表示（診断は diagnosis.py）。計時は build / transform（CPMpy→ソルバー変換）/ solve に分けて返す。"""
from __future__ import annotations
import time
from typing import Any, NamedTuple
import numpy as np
import cpmpy as cp
from .config import load_settings
from .instance import Instance, Eps
from .model import NurseModel


# 決定事項: 求解は CP-SAT（OR-Tools）。MUS の計算は exact（diagnosis.MUS_SOLVER）。変更する場合はユーザーに確認する。
SOLVE_SOLVER = "ortools"


class SolveResult(NamedTuple):
    """solve の戻り値。従来どおりタプルとして展開もできる: ok, status, M, times, counts = solve(...)"""
    ok: bool                       # 解が見つかったか
    status: Any                    # ソルバーの状態（status().exitstatus で名前）
    model: NurseModel              # 変数を持つモデル（解の読み出しに使う）
    times: dict[str, float]        # build（式の生成）/ transform（ソルバーへの変換）/ solve（求解）の秒数
    counts: dict[str, int]         # 制約群ごとの制約本数


def solve(I: Instance, eps: Eps, time_limit: float | None = None, workers: int = 8, lin_level: int = 1,
          solver: str = SOLVE_SOLVER, enabled: set[str] | None = None) -> SolveResult:
    """入力データから制約モデルを作って解く（目的関数なし。すべての制約を満たす勤務表を1つ探す）。

    Args:
        I: 入力データ。
        eps: ε 制約（品質要件）の閾値。
        time_limit: 求解の時間制限（秒）。省略すると nurse.properties の `solve.time_limit`（既定30秒）を使う。
        workers: 並列に探索するワーカー数（ortools のみ）。
        lin_level: 線形緩和の強さ 0/1/2（ortools の `linearization_level`。CLI の `--lin`）。
        solver: 既定は `SOLVE_SOLVER` = "ortools"（CP-SAT）。"exact" / "pysat" は実験用。
        enabled: 有効にする制約名（None=全て）。カーネル・層1は外せない（`Rules.off` と同じ規則）。

    Returns:
        `SolveResult`。従来どおり `ok, status, M, times, counts = solve(...)` と展開もできる。"""
    if time_limit is None:
        time_limit = load_settings().solve_time_limit
    t0 = time.perf_counter()
    M = NurseModel(I, eps)
    groups = M.build(enabled)
    model = cp.Model([c for cs in groups.values() for c in cs])
    t1 = time.perf_counter()
    s = cp.SolverLookup.get(solver, model)
    t2 = time.perf_counter()
    if solver == "ortools":
        ok = s.solve(time_limit=time_limit, num_workers=workers, linearization_level=lin_level)
    else:
        ok = s.solve(time_limit=time_limit)
    t3 = time.perf_counter()
    return SolveResult(ok, s.status(), M, dict(build=t1 - t0, transform=t2 - t1, solve=t3 - t2),
                       {k: len(v) for k, v in groups.items()})


def roster(M: NurseModel) -> list[str]:
    """勤務表の文字列表示。解の読み出しは x からのみ（y は片側定義のため値を使わない）

    Args:
        M: 求解後の `NurseModel`。

    Returns:
        看護師ごとの1行の文字列のリスト（`N01 休 日勤 …`）。"""
    rows = []
    for n in M.N:
        cells = []
        for d in range(M.T):
            j = int(np.argmax(M.xv[n][M.t(d), :].value()))
            cells.append(M.I.shifts[M.dom[n][j]].name)
        rows.append(f"N{n:02d} " + " ".join(f"{c:>4s}" for c in cells))
    return rows
