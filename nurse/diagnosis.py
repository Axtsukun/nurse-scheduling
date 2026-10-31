"""
解けないときの診断。原因は2つに分けて扱う:
  - 時間切れ … 解も、解が無い証明も得られなかった（制約矛盾かどうかは不明）
  - 制約矛盾 … 解が無いと証明できた。同時には成り立たない最小の制約の集合（MUS）を日本語で説明する

MUS（極小充足不能部分集合）の求め方
  - cpmpy.tools.mus を、PB ソルバー exact で使う（決定事項。実測で CP-SAT より速い。README 4.5 節）。
    求解は CP-SAT（solver.SOLVE_SOLVER）、MUS は exact（MUS_SOLVER）と決めており、切り替えの自動化はしない。
  - 制約を hard と soft に分けて渡す（hard は仮定を持たないので、soft だけが絞り込みの対象になり速い）。
      背景 … 構造・履歴・補助変数の定義（relaxable=False の群と Aux）。常に成り立たせる
      前提 … カーネル・層1の制約（固定割当・労基法の休日）。scope が MANDATORY = オフにできない
      soft … 層2・層3の制約（scope が TOGGLEABLE = 外せる）。矛盾の説明の主役
  - 一段階で求める（制約群を単位にした先行の絞り込みは、実測で一段階より遅かったので採用しない）。
  - 時間制限（mus_time_limit）: cpmpy.tools.mus 自体には時間制限がなく、極端な矛盾では戻らない。そこで
      ① 事前候補: MUS の前に、仮定つきの求解 1 回で矛盾の集合（コア。最小とは限らない）を取る。
      ② mus を子プロセスで実行し、mus_time_limit 秒で打ち切る。終われば最小（minimal=True）。
      ③ 打ち切ったら、①の事前候補を結果にする（minimal=False。矛盾する集合だが、不要な制約を含む可能性がある）。
    ①が時間内に終わらなければ、矛盾は確定しているが原因の制約は特定できない（items が空、minimal=False）。
  - CPMpy は制約式に名前を付けられないので、式の id から Named（制約群・日本語ラベル）を引き戻す。
"""
from __future__ import annotations
from dataclasses import dataclass, field
import multiprocessing
import time
import cpmpy as cp
from cpmpy.tools import mus
from cpmpy.tools.explain.utils import make_assump_model
from .instance import Instance, Eps
from .registry import REGISTRY, MANDATORY, TOGGLEABLE, Named
from .solver import solve, SOLVE_SOLVER

MUS_SOLVER = "exact"                       # 決定事項: MUS の計算は exact。変更する場合はユーザーに確認する
MUS_TIME_LIMIT = 60.0                      # MUS 1回あたりの時間制限（秒）。soft の MUS と前提の MUS にそれぞれ適用
FEASIBLE, TIMEOUT, CONFLICT = "feasible", "timeout", "conflict"
SCOPE_NAMES = {"core": "カーネル", "law": "層1 労働法規", "domain": "層2 病院・看護職共通", "optional": "層3 任意"}


@dataclass
class Diagnosis:
    """診断の結果。status は FEASIBLE / TIMEOUT / CONFLICT のいずれか"""
    status: str
    detail: str = ""                                          # ソルバーの状態など
    items: list[Named] = field(default_factory=list)          # CONFLICT: MUS を構成する個別の制約（定義順）
    hard_only: bool = False                                   # CONFLICT: True ならオフにできない制約（前提）同士の矛盾
    times: dict[str, float] = field(default_factory=dict)     # solve / mus_soft / mus_given の秒数
    mus_solver: str = ""                                      # MUS に使ったソルバー
    minimal: bool = True                                      # CONFLICT: False なら時間制限で打ち切り（最小でない可能性。items が空なら特定できず）

    @property
    def groups(self) -> list[str]:
        """MUS に含まれる制約群名（登録順）"""
        names = {x.group for x in self.items}
        return [g for g in REGISTRY if g in names]

    @property
    def given_items(self) -> list[Named]:
        """MUS のうち、オフにできない制約（カーネル・層1。前提として固定したもの）"""
        return [x for x in self.items if REGISTRY[x.group].scope in MANDATORY]

    @property
    def soft_items(self) -> list[Named]:
        """MUS のうち、外せる制約（層2・層3）"""
        return [x for x in self.items if REGISTRY[x.group].scope in TOGGLEABLE]

    def explain(self) -> str:
        """診断結果の日本語の説明"""
        return explain(self)


def _require_solver(name: str) -> str:
    """MUS に使うソルバーが導入済みか確かめる。未導入なら（黙って別のソルバーに替えず）RuntimeError"""
    if name not in cp.SolverLookup.supported():
        raise RuntimeError(f"MUS ソルバー {name!r} が使えない（導入済み: {cp.SolverLookup.supported()}）。"
                           "exact は `pip install exact` で入れる")
    return name


def _mus_in_child(soft: list, hard: list, solver: str, budget: float):
    """cpmpy.tools.mus を fork した子プロセスで実行し、budget 秒で打ち切る。戻り値は soft の添字の列、打ち切りなら None。
    子は親のメモリをそのまま引き継ぐ（式を pickle しない）ので、結果は soft の添字で返す。
    fork が使えない環境では、時間制限を黙って外さず RuntimeError にする。"""
    if "fork" not in multiprocessing.get_all_start_methods():
        raise RuntimeError("MUS の時間制限には fork が必要（この環境では使えない）。mus_time_limit を使うには Linux などで実行する")
    ctx = multiprocessing.get_context("fork")
    index = {id(e): i for i, e in enumerate(soft)}

    def run(conn) -> None:
        try:
            conn.send(("ok", [index[id(c)] for c in mus(soft, hard, solver=solver)]))
        except BaseException as e:                                  # 子の失敗は親に伝える
            conn.send(("error", repr(e)))

    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=run, args=(send,), daemon=True)
    proc.start()
    send.close()
    try:
        if not recv.poll(budget):                                   # 時間切れ: 子を止める
            return None
        kind, payload = recv.recv()
    except EOFError:
        raise RuntimeError("MUS の子プロセスが結果を返さずに終了した") from None
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join()
    if kind == "error":
        raise RuntimeError(f"MUS の計算が失敗した: {payload}")
    return payload


def _bounded_mus(soft: list, hard: list, solver: str, budget: float):
    """時間制限つきの MUS。戻り値は (矛盾する制約の式の列, 最小か)。事前候補も取れなければ None。
    ① 仮定つきの求解 1 回でコア（事前候補。最小とは限らない）を取る。② mus を budget 秒で実行。終われば最小。
    ③ 打ち切りなら、①の事前候補を返す（最小でない可能性）。"""
    model, exprs, assump = make_assump_model(soft, hard=hard)
    s = cp.SolverLookup.get(solver, model)
    by_assump = dict(zip(assump, exprs))
    if s.solve(assumptions=assump, time_limit=budget):
        raise RuntimeError("MUS: 矛盾するはずのモデルが充足可能だった")
    if s.status().exitstatus.name != "UNSATISFIABLE":
        return None                                                 # 事前候補が時間内に取れなかった
    candidate = [by_assump[a] for a in s.get_core()]
    found = _mus_in_child(soft, hard, solver, budget)
    if found is None:
        return candidate, False
    return [soft[i] for i in found], True


def _check(exprs: list, time_limit: float) -> str:
    """exprs が充足可能かを求解用のソルバー（CP-SAT）で調べる。"sat" / "unsat" / "unknown"（時間切れ）"""
    s = cp.SolverLookup.get(SOLVE_SOLVER, cp.Model(exprs))
    if s.solve(time_limit=time_limit):
        return "sat"
    return "unsat" if s.status().exitstatus.name == "UNSATISFIABLE" else "unknown"


def diagnose(I: Instance, eps: Eps, time_limit: float = 30, mus_solver: str = MUS_SOLVER,
             mus_time_limit: float = MUS_TIME_LIMIT) -> Diagnosis:
    """解けるか調べ、解けなければ原因（時間切れ / 制約矛盾）を返す。
    1. 通常のモデルを求解用のソルバー（CP-SAT）で解く。解ければ FEASIBLE、時間切れなら TIMEOUT（矛盾かどうかは不明）。
    2. 実行不能と確定したときだけ MUS を求める（指示変数つきのモデルは桁違いに遅いので、解ける問題では解かない）。
       a. 背景と前提（カーネル・層1）だけで矛盾するなら、前提の中から MUS（hard_only=True。入力データと法令の矛盾）。
       b. そうでなければ、前提を hard として固定し、soft（層2・層3）の MUS を求め、
          その矛盾に関わる前提（例: 固定割当）を追加で絞り込む。
    MUS は mus_time_limit 秒（soft と前提でそれぞれ）で打ち切る。打ち切ると、事前候補（最小とは限らない矛盾の集合）を
    返し、minimal=False にする。"""
    first = solve(I, eps, time_limit=time_limit)
    times = {"solve": first.times["solve"]}
    state = first.status.exitstatus.name
    if first.ok:
        return Diagnosis(FEASIBLE, state, times=times)
    if state != "UNSATISFIABLE":
        return Diagnosis(TIMEOUT, f"{time_limit:g}秒で解も矛盾の証明も得られなかった（{state}）", times=times)

    flat = [x for items in first.model.build_named().values() for x in items]
    background = [x.expr for x in flat if x.hard]
    given = [x for x in flat if not x.hard and REGISTRY[x.group].scope in MANDATORY]
    soft = [x for x in flat if not x.hard and REGISTRY[x.group].scope in TOGGLEABLE]
    solver = _require_solver(mus_solver)
    position = {id(x.expr): i for i, x in enumerate(flat)}              # 定義順（日付順など）で並べる

    def pick(core, pool: list[Named]) -> list[Named]:
        by_id = {id(x.expr): x for x in pool}
        return sorted((by_id[id(c)] for c in core if id(c) in by_id), key=lambda x: position[id(x.expr)])

    def run(soft_exprs: list, hard_exprs: list, pool: list[Named]):
        """時間制限つきの MUS → (pool のうち矛盾に関わるもの, 最小か)。事前候補も取れなければ (None, False)"""
        got = _bounded_mus(soft_exprs, hard_exprs, solver, mus_time_limit)
        return (None, False) if got is None else (pick(got[0], pool), got[1])

    t0 = time.perf_counter()
    given_exprs = [x.expr for x in given]
    verdict = _check(background + given_exprs, time_limit)
    if verdict == "unknown":
        return Diagnosis(TIMEOUT, f"オフにできない制約だけの充足判定が{time_limit:g}秒で終わらなかった", times=times)
    if verdict == "unsat":                                              # a. 前提同士の矛盾（soft を外す余地がない）
        if not given:
            raise RuntimeError("背景の制約だけで矛盾している（モデルの不具合）")
        items, minimal = run(given_exprs, background, given)
        times["mus_given"] = time.perf_counter() - t0
        return Diagnosis(CONFLICT, state, items or [], True, times, solver, minimal)

    soft_items, minimal = run([x.expr for x in soft], background + given_exprs, soft)       # b. soft の MUS（一段階）
    times["mus_soft"] = time.perf_counter() - t0
    if soft_items is None:                                              # 矛盾は確定しているが、原因を特定できなかった
        return Diagnosis(CONFLICT, state, [], False, times, solver, False)
    t1 = time.perf_counter()
    involved = []                                                       # soft の矛盾に関わる前提だけに絞る
    if given:
        involved, minimal_given = run(given_exprs, background + [x.expr for x in soft_items], given)
        if involved is None:                                            # 絞れなければ前提は載せない（soft の説明だけ）
            involved, minimal_given = [], False
        minimal = minimal and minimal_given
    times["mus_given"] = time.perf_counter() - t1
    items = sorted(involved + soft_items, key=lambda x: position[id(x.expr)])
    return Diagnosis(CONFLICT, state, items, False, times, solver, minimal)


def explain(d: Diagnosis) -> str:
    """Diagnosis の日本語の説明文"""
    if d.status == FEASIBLE:
        return "【診断】解が見つかりました（制約の矛盾はありません）。"
    if d.status == TIMEOUT:
        return ("【診断】時間切れ: 解も、解が無い証明も得られませんでした（制約矛盾かどうかは不明です）。\n"
                f"  状態: {d.detail}\n"
                "  対処: time_limit を延ばす、規模を小さくする、night_band など求解を重くする条件を見直す、"
                "precheck(I, eps) で需要と供給の単純な不足がないか確かめる。")
    if not d.items:                                                    # 矛盾は確定、原因は特定できなかった（時間制限）
        return ("【診断】制約矛盾: 解が存在しません（証明済み）。ただし、矛盾の原因になっている制約を時間内に特定できませんでした。\n"
                "  対処: mus_time_limit を延ばす、規模を小さくする、制約群を Rules.off で外して絞り込む。")
    lines = ["【診断】制約矛盾: 解が存在しません。次の制約は同時には成り立ちません。"]
    lines.append("  （最小不整合部分集合 MUS: このうち1つでも外せば、残りは矛盾しなくなります）" if d.minimal else
                 "  （時間制限で打ち切ったため、最小でない可能性があります。矛盾する集合ですが、不要な制約を含むことがあります）")
    lines.append("")
    lines.append(f"■ 関係する制約群（{len(d.groups)}群）")
    for g in d.groups:
        sp = REGISTRY[g]
        src = f" 根拠: {', '.join(sp.source)}" if sp.source else ""
        lines.append(f"  - {g}: {sp.title or g}（{SCOPE_NAMES[sp.scope]}）{src}")

    def block(title: str, items: list[Named]) -> None:
        lines.extend(["", f"■ {title}（{len(items)}件）"])
        lines.extend(f"  {x.line}" for x in items)             # `{ID}  [{クラス名}] {メッセージ}`。ID は一意なので集約しない

    if d.hard_only:
        block("矛盾する制約: オフにできない制約同士（入力データと法令）", d.items)
    else:
        if d.given_items:
            block("前提としている制約（オフにできない。カーネル・層1）", d.given_items)
        block("矛盾の原因になっている制約（外せる。層2・層3）", d.soft_items)

    lines += ["", "■ 対処"]
    if d.hard_only:
        lines.append("  - これらはオフにできません。入力データ（固定割当・需要・公休数など）を見直してください。")
    else:
        lines.append(f"  - 外せる制約（{', '.join(g for g in d.groups if REGISTRY[g].scope in TOGGLEABLE)}）は、"
                     "パラメータ（需要・上限など）の調整か、Rules.off での除外で緩められます。")
        if d.given_items:
            lines.append(f"  - 前提の制約（{', '.join(g for g in d.groups if REGISTRY[g].scope in MANDATORY)}）はオフにできません。"
                         "入力データ（固定割当など）を見直す手もあります。")
    t = d.times
    lines += ["", f"（計算: 通常の求解 {t.get('solve', 0):.1f}秒 / soft のMUS {t.get('mus_soft', 0):.1f}秒 / "
                  f"前提のMUS {t.get('mus_given', 0):.1f}秒、MUSソルバー {d.mus_solver}）"]
    return "\n".join(lines)
