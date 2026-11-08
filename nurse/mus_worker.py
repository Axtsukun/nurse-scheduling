"""MUS プロセス: 診断が書いた request を読み、矛盾の原因（MUS）を求めて、結果をファイルで返す。

    python -m nurse.mus_worker 作業ディレクトリ

診断（`nurse.diagnosis`）が、MUS の計算をこのプロセスに任せる。**exact を含む MUS のソルバーは、このプロセスの中だけで動く**
（診断を呼ぶプロセスは exact を読み込まない）。やりとりするファイルは `nurse.mus_protocol` のとおり。

手順（時間制限は、診断がこのプロセスを止めることで掛ける。`cpmpy.tools.mus` 自体には時間制限がないため）:

1. request を読み、`ready` を作る（診断はここから時間制限を数える）。
2. **事前候補**: 仮定つきの求解 1 回で、矛盾する集合（コア。最小とは限らない）を取り、`core.pkl` に書く。
3. **MUS**: `cpmpy.tools.mus` で最小にする。終わったら `response.pkl` に書く。

- 結果は soft の添字で返す（式そのものは返さない）。制約や変数名は、標準出力・標準エラー・メッセージのどこにも書かない
  （失敗の理由は、例外のクラス名だけ）。
- `python -O` では `assert` が無効になり、`mus` の「モデルは充足不能でなければならない」検査が効かない。
  そのため `-O`（と `PYTHONOPTIMIZE`）で起動されたら ERROR を返す。診断は、これらを外して起動する。
"""
from __future__ import annotations
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
import cpmpy as cp
from cpmpy.expressions.core import Expression
from cpmpy.tools import mus
from cpmpy.tools.explain.utils import make_assump_model
from .mus_protocol import (CORE_FILE, ERROR, MUS, READY_FILE, REQUEST_FILE, REQUEST_KEYS, RESPONSE_FILE, SAT, UNAVAILABLE, UNKNOWN,
                           ProtocolError, make_core, make_response, read_message, write_message)


def _indices(found: list[Expression], soft: list[Expression]) -> list[int] | None:
    """式の列を、soft の添字の列にする（式の同一性で引く）

    Args:
        found: `mus` や `get_core` が返した式。
        soft: request の soft（添字の基準）。

    Returns:
        soft の添字（昇順）。soft にない式があって対応づけられないときは `None`。"""
    index = {id(c): i for i, c in enumerate(soft)}
    out = [index[id(c)] for c in found if id(c) in index]
    return sorted(out) if len(out) == len(found) else None


def solve_request(req: dict[str, Any], on_core: Callable[[list[int], float], None]) -> dict[str, Any]:
    """request から response を作る。事前候補が取れたら、MUS を求める前に `on_core` で知らせる

    Args:
        req: request の辞書（`nurse.mus_protocol.make_request` の形）。
        on_core: 事前候補（soft の添字・事前候補の求解の秒数）を受け取る関数。診断が、MUS の打ち切り時にこれを使う。

    Returns:
        response の辞書。例外は ERROR の response にする（message は例外のクラス名だけ）。"""
    solver, timings = str(req["solver"]), {"core": 0.0, "mus": 0.0}

    def reply(status: str, indices: list[int] | None = None, message: str = "") -> dict[str, Any]:
        """response を作る（秒数の内訳は、その時点までの値）

        Args:
            status: response の状態。
            indices: MUS に含まれる soft の添字。
            message: 失敗の理由など。

        Returns:
            response の辞書。"""
        return make_response(status, indices or [], timings, message)

    if not __debug__:
        return reply(ERROR, message="python -O では動かない（assert が無効になり、充足可能を検出できない）。-O と PYTHONOPTIMIZE を外して起動する")
    supported = cp.SolverLookup.supported()
    if solver not in supported:
        return reply(UNAVAILABLE, message=f"MUS ソルバー {solver!r} が使えない（導入済み: {supported}）。exact は `pip install exact` で入れる")
    hard, soft = req["hard"], req["soft"]
    if not isinstance(soft, list) or not isinstance(hard, list):
        return reply(ERROR, message="hard と soft はリストでなければならない")
    # MUS の結果を添字に戻すには、soft の各要素が 1 つの CPMpy 式で、同じオブジェクトが重複しないことが前提
    # （診断は、`Named.expr` が常に式で、どの 2 つの制約も式を共有しないようにしている）。
    not_expr, dup = sum(1 for c in soft if not isinstance(c, Expression)), len(soft) - len({id(c) for c in soft})
    if not_expr or dup:
        return reply(ERROR, message=f"soft の要素が不正: 式でない要素 {not_expr} 件、同じオブジェクトの重複 {dup} 件")
    try:
        t0 = time.perf_counter()
        model, exprs, assump = make_assump_model(soft, hard=hard)
        s = cp.SolverLookup.get(solver, model)
        by_assump = dict(zip(assump, exprs))
        if s.solve(assumptions=assump, time_limit=req["time_limit"]):
            return reply(SAT, message="モデルは充足可能（MUS はない）")
        timings["core"] = time.perf_counter() - t0
        if s.status().exitstatus.name != "UNSATISFIABLE":
            return reply(UNKNOWN, message="事前候補が時間内に取れなかった")
        core = _indices([by_assump[a] for a in s.get_core()], soft)
        if core is None:
            return reply(ERROR, message="事前候補の要素を soft の添字に対応づけられない")
        on_core(core, timings["core"])
        t1 = time.perf_counter()
        found = _indices(mus(soft, hard=hard, solver=solver), soft)
        timings["mus"] = time.perf_counter() - t1
        if found is None:
            return reply(ERROR, message="MUS の要素を soft の添字に対応づけられない（mus が soft を作り直した可能性）")
        return reply(MUS, found)
    except Exception as e:                                                # 想定外（内容は書かず、クラス名だけ）
        return reply(ERROR, message=type(e).__name__)


def main(argv: list[str] | None = None) -> int:
    """コマンドライン。例外が出ても、可能な限り response を書く

    Args:
        argv: 引数（省略すると `sys.argv[1:]`）。作業ディレクトリ 1 つだけ。

    Returns:
        終了コード（response を書けたら 0。引数の誤りは 2）。"""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("使い方: python -m nurse.mus_worker 作業ディレクトリ", file=sys.stderr)
        return 2
    work = Path(args[0])
    try:
        req = read_message(work / REQUEST_FILE, REQUEST_KEYS, "request")
        (work / READY_FILE).touch()
        resp = solve_request(req, lambda core, elapsed: write_message(work / CORE_FILE, make_core(core, elapsed)))
    except ProtocolError as e:                                            # request が読めない・形が違う
        resp = make_response(ERROR, [], {}, str(e))
    except Exception as e:                                                # 想定外
        resp = make_response(ERROR, [], {}, f"想定外のエラー: {type(e).__name__}")
    write_message(work / RESPONSE_FILE, resp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
